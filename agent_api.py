"""agent_api.py — the programmatic surface for driving this pipeline from an agent.

Every function here returns a plain, JSON-serializable dict (or list of dicts):
no Path objects, no datetimes, no exceptions leaking out. That makes each one
safe to expose directly as a tool to a model, to serialize into a tool_result,
or to call from any orchestrator. Human-facing CLIs (scan.py, pipeline.py, ...)
still exist and are unchanged; this module is the machine-facing twin.

Design rules:
  * Return, never raise. Failures come back as {"ok": False, "error": "..."}.
  * Everything JSON-safe. Paths are returned as strings.
  * Distinguish "nothing matched" (ok, empty) from "something broke" (not ok).
  * No printing. The agent decides what to say to the user.

The functions map to the five things an agent needs to run the pipeline:
  discover   -> scan_jobs, read_linkedin_alerts, ingest_batch
  configure  -> list_watchlist, add_watchlist_company, check_watchlist
  triage     -> analyze_job
  produce    -> generate_documents
  track      -> list_applications
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import yaml

import config
import sources

WATCHLIST_PATH = config.ROOT / "watchlist.yaml"
VALID_ATS = sorted(sources.PROVIDERS.keys())


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------
def _load_watchlist() -> dict:
    return yaml.safe_load(WATCHLIST_PATH.read_text())


def _load_profile() -> dict:
    with open(config.PROFILE_PATH) as fh:
        return yaml.safe_load(fh)


def _client():
    """Build an Anthropic client, or return None if the SDK/key is unavailable."""
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception:  # noqa: BLE001
        return None


def _job_public(j: dict) -> dict:
    """Trim an internal job dict to the fields an agent should see."""
    return {
        "company": j.get("company", ""),
        "title": j.get("title", ""),
        "location": j.get("location", ""),
        "url": j.get("url", ""),
        "source": j.get("source", ""),
        "prescore": j.get("_prescore"),
        "description": (j.get("description") or "")[:1500],
    }


def _analysis_public(a: dict) -> dict:
    kg = a.get("keyword_gap", {}) or {}
    return {
        "company": a.get("company"),
        "title": a.get("title"),
        "location": a.get("location"),
        "remote": a.get("remote"),
        "salary_low": a.get("salary_low"),
        "salary_high": a.get("salary_high"),
        "fit_score": a.get("fit_score"),
        "recommendation": a.get("recommendation"),
        "best_angle": a.get("best_angle"),
        "reasons": a.get("reasons", []),
        "dealbreakers": a.get("dealbreakers", []),
        "hard_requirements": a.get("hard_requirements", []),
        "keyword_coverage": kg.get("coverage"),
        "keyword_priority_missing": kg.get("priority_missing", []),
        "keyword_missing": kg.get("missing", []),
    }


# ---------------------------------------------------------------------------
# DISCOVER
# ---------------------------------------------------------------------------
def scan_jobs(analyze: bool = False, top: int = 8,
              include_seen: bool = False, mark_seen: bool = True) -> dict:
    """Poll the watchlist, filter, dedupe, rank, and (optionally) score matches.

    Returns:
        {ok, polled, fetched, matched, new, jobs:[...], errors:[...]}
      - errors distinguishes an unreachable board / bad slug from an empty one.
      - when analyze=True, the top `top` jobs also carry fit_score,
        recommendation and keyword_coverage.
    """
    import scan

    try:
        wl = _load_watchlist()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"could not read watchlist.yaml: {e}"}

    companies = wl.get("companies", [])
    # per-company fetch (quiet): captures structured errors instead of printing
    errors: list[dict] = []
    jobs: list[dict] = []
    for entry in companies:
        jobs.extend(sources.fetch_company(entry, errors))

    matched = scan.filter_jobs(jobs, wl)
    seen = scan.load_seen()
    new = [j for j in matched if scan.job_key(j) not in seen]
    working = matched if include_seen else new
    for j in working:
        j["_prescore"] = scan.prescore(j, wl)
    working.sort(key=lambda j: j["_prescore"], reverse=True)

    out = [_job_public(j) for j in working]

    if analyze and working:
        client = _client()
        if client is None:
            return {"ok": False,
                    "error": "analyze=True needs the anthropic SDK and "
                             "ANTHROPIC_API_KEY; scan without analyze succeeded",
                    "polled": len(companies), "fetched": len(jobs),
                    "matched": len(matched), "new": len(new), "jobs": out,
                    "errors": errors}
        import analyze as analyzer
        profile = _load_profile()
        for pub, j in list(zip(out, working))[:top]:
            try:
                a = analyzer.analyze(scan.as_job_text(j), profile, client=client)
                pub["fit_score"] = a.get("fit_score")
                pub["recommendation"] = a.get("recommendation")
                pub["keyword_coverage"] = (a.get("keyword_gap") or {}).get("coverage")
            except Exception as e:  # noqa: BLE001
                pub["analyze_error"] = str(e)

    if mark_seen and working:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for j in working:
            seen[scan.job_key(j)] = {"title": j["title"], "company": j["company"],
                                     "first_seen": now}
        scan.save_seen(seen)

    return {"ok": True, "polled": len(companies), "fetched": len(jobs),
            "matched": len(matched), "new": len(new),
            "jobs": out, "errors": errors}


def read_linkedin_alerts(days: int = 7) -> dict:
    """Parse LinkedIn job-alert emails over IMAP into a list of postings.

    Requires MAIL_HOST / MAIL_USER / MAIL_PASS in the environment. Returns
    {ok, emails_read, jobs:[...]} or {ok: False, error} if mail isn't set up.
    """
    import os
    if not all(os.environ.get(k) for k in ("MAIL_HOST", "MAIL_USER", "MAIL_PASS")):
        return {"ok": False,
                "error": "set MAIL_HOST, MAIL_USER and MAIL_PASS to read alerts"}
    try:
        import linkedin_inbox
        jobs, n_mail = linkedin_inbox.fetch(days)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"could not read mail: {e}"}
    return {"ok": True, "emails_read": n_mail,
            "jobs": [{"title": j["title"], "company": j["company"],
                      "location": j["location"], "url": j["url"],
                      "source": j["source"]} for j in jobs]}


def ingest_batch(text: str, generate: bool = False) -> dict:
    """Split a pasted blob of several postings, analyze each, rank them.

    For sources the pollers can't reach (LinkedIn/Workday postings). Returns
    {ok, count, results:[...]} where each result is an analysis summary.
    """
    import paste_batch
    postings = paste_batch.split_blob(text)
    if not postings:
        return {"ok": True, "count": 0, "results": [],
                "note": "no postings found; separate them with a line of ---"}
    client = _client()
    if client is None:
        return {"ok": False, "error": "needs the anthropic SDK and ANTHROPIC_API_KEY",
                "count": len(postings)}
    import analyze as analyzer
    profile = _load_profile()
    results = []
    for chunk in postings:
        try:
            a = analyzer.analyze(chunk, profile, client=client)
        except Exception as e:  # noqa: BLE001
            results.append({"ok": False, "error": str(e)})
            continue
        summary = _analysis_public(a)
        if generate and a.get("recommendation") != "skip":
            summary["documents"] = generate_documents(chunk, _analysis=a, _client=client)
        results.append(summary)
    results.sort(key=lambda r: r.get("fit_score") or 0, reverse=True)
    return {"ok": True, "count": len(postings), "results": results}


# ---------------------------------------------------------------------------
# CONFIGURE
# ---------------------------------------------------------------------------
def list_watchlist() -> dict:
    """Return the companies and filters currently in watchlist.yaml."""
    try:
        wl = _load_watchlist()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True,
            "companies": wl.get("companies", []),
            "title_include": wl.get("titles", {}).get("include", []),
            "title_exclude": wl.get("titles", {}).get("exclude", []),
            "locations": wl.get("location", {}).get("allow", [])}


def add_watchlist_company(name: str, ats: str, slug: str) -> dict:
    """Append a company to watchlist.yaml, preserving comments and layout.

    Inserts the entry just before the top-level `titles:` block. Validates the
    ats against the known providers. Does NOT verify the slug resolves — call
    check_watchlist() afterward for that.
    """
    ats = (ats or "").lower().strip()
    if ats not in VALID_ATS:
        return {"ok": False, "error": f"unknown ats '{ats}'; use one of {VALID_ATS}"}
    if not slug or not slug.strip():
        return {"ok": False, "error": "slug is required"}
    slug = slug.strip()

    # idempotent: if this ats+slug is already present, don't add a duplicate
    try:
        current = _load_watchlist().get("companies", [])
        if any(c.get("slug") == slug and (c.get("ats") or "").lower() == ats
               for c in current):
            return {"ok": True, "added": {"name": name, "ats": ats, "slug": slug},
                    "note": "already in watchlist", "total_companies": len(current)}
    except Exception:  # noqa: BLE001
        pass

    text = WATCHLIST_PATH.read_text()
    entry = (f"  - name: {name}\n    ats: {ats}\n    slug: {slug}"
             f"    # added by agent\n")
    marker = "\ntitles:\n"
    if marker not in text:
        return {"ok": False, "error": "could not locate the titles: block"}
    text = text.replace(marker, "\n" + entry + marker, 1)

    # validate the result still parses before writing
    try:
        parsed = yaml.safe_load(text)
        assert any(c.get("slug") == slug and c.get("ats") == ats
                   for c in parsed.get("companies", []))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"edit would break the file, aborted: {e}"}
    WATCHLIST_PATH.write_text(text)
    return {"ok": True, "added": {"name": name, "ats": ats, "slug": slug},
            "total_companies": len(parsed.get("companies", []))}


def check_watchlist() -> dict:
    """Fetch every board once and report which slugs resolve and which don't."""
    try:
        wl = _load_watchlist()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    resolved, unresolved = [], []
    for entry in wl.get("companies", []):
        errs: list[dict] = []
        jobs = sources.fetch_company(entry, errs)
        row = {"name": entry.get("name"), "ats": entry.get("ats"),
               "slug": entry.get("slug"), "postings": len(jobs)}
        if jobs or not errs:
            resolved.append(row)
        else:
            row["error"] = errs[0].get("error")
            unresolved.append(row)
    return {"ok": True, "resolved": resolved, "unresolved": unresolved}


# ---------------------------------------------------------------------------
# TRIAGE
# ---------------------------------------------------------------------------
def analyze_job(job_text: str) -> dict:
    """Score one posting against the profile: fit, recommendation, keyword gap."""
    if not job_text or len(job_text) < 80:
        return {"ok": False, "error": "job_text is empty or too short"}
    client = _client()
    if client is None:
        return {"ok": False, "error": "needs the anthropic SDK and ANTHROPIC_API_KEY"}
    try:
        import analyze as analyzer
        a = analyzer.analyze(job_text, _load_profile(), client=client)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    result = _analysis_public(a)
    result["ok"] = True
    return result


# ---------------------------------------------------------------------------
# PRODUCE
# ---------------------------------------------------------------------------
def generate_documents(job_text: str, _analysis=None, _client=None) -> dict:
    """Analyze (if needed), build a tailored resume + cover letter, lint the resume.

    Returns {ok, resume, cover_letter, ats_lint, fit_score, recommendation}
    with file paths as strings.
    """
    client = _client_or(_client)
    if client is None:
        return {"ok": False, "error": "needs the anthropic SDK and ANTHROPIC_API_KEY"}
    try:
        import analyze as analyzer
        import generate
        import tracker
        from pipeline import keywords_from
        profile = _load_profile()
        a = _analysis or analyzer.analyze(job_text, profile, client=client)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        docs = generate.build_both(profile, a["best_angle"], job=a,
                                   keywords=keywords_from(a))
        cover = generate.build_cover_letter(profile, a, client=client)
        verdict = None
        try:
            import ats_lint
            verdict = ats_lint.lint(str(docs["ats"])).get("verdict")
        except Exception:  # noqa: BLE001
            pass
        tracker.record(a, resume_file=docs["polished"], cover_file=cover)
        return {"ok": True, "resume": str(docs["polished"]),
                "resume_ats": str(docs["ats"]), "cover_letter": str(cover),
                "ats_lint": verdict, "fit_score": a.get("fit_score"),
                "recommendation": a.get("recommendation")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _client_or(existing):
    return existing if existing is not None else _client()


# ---------------------------------------------------------------------------
# TRACK
# ---------------------------------------------------------------------------
def list_applications(status: str = None) -> dict:
    """Read the application tracker CSV. Optionally filter by status."""
    path = config.TRACKER_PATH
    if not path.exists():
        return {"ok": True, "applications": [], "note": "no applications logged yet"}
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if status and (row.get("status") or "").lower() != status.lower():
                continue
            rows.append(dict(row))
    return {"ok": True, "count": len(rows), "applications": rows}
