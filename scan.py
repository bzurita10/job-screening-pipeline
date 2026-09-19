#!/usr/bin/env python3
"""scan.py — poll the company watchlist and surface NEW matching postings.

This is the front end of the pipeline: instead of you finding a job and pasting
a link or screenshot, the scanner polls every company board in watchlist.yaml,
filters by your title and location rules, drops anything it has shown you
before, and prints a ranked shortlist. With --analyze it runs the survivors
through the existing analyzer (fit score + ATS keyword gap).

Usage:
    python scan.py                 # poll, filter, list what's new
    python scan.py --check         # verify every slug in the watchlist resolves
    python scan.py --all           # include postings already seen
    python scan.py --analyze       # score the new matches (uses the Claude API)
    python scan.py --analyze --top 5 --generate   # score top 5 and build resumes
    python scan.py --save-dir jobs/inbox          # write each match to a .txt

Seen-posting state lives in data/seen_jobs.json (git-ignored).
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

import yaml

import config
import sources

SEEN_PATH = config.DATA_DIR / "seen_jobs.json"
WATCHLIST_PATH = config.ROOT / "watchlist.yaml"


# ----------------------------------------------------------------------------
# state
# ----------------------------------------------------------------------------
def load_seen() -> dict:
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_seen(seen: dict):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=2))


def job_key(job: dict) -> str:
    """Stable identity for a posting: prefer the URL, else company+title."""
    return job.get("url") or f"{job['company']}::{job['title']}".lower()


# ----------------------------------------------------------------------------
# filtering
# ----------------------------------------------------------------------------
_RANGE_K = re.compile(r"\$\s?(\d{1,3})\s?(?:-|\u2013|to)\s?(\d{1,3})\s?[kK]\b")
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d+)(\.\d+)?([kKmMbB])?(?![a-zA-Z])")


def parse_salary(text: str):
    """Best-effort ANNUAL salary from a posting -> (low, high) in USD, or
    (None, None) when nothing salary-like is found.

    Deliberately conservative: only counts $-prefixed amounts that resolve into
    $30k-$2M. Skips 401(k) and M/B suffixes (AUM / ARR / funding language), and
    won't read the leading letter of the next word ('$220,000 base') as a suffix.
    A missing salary reads as unlisted, never as a low number.
    """
    if not text:
        return None, None
    band = []
    for m in _RANGE_K.finditer(text):          # "$120-150K": $ on first, K on last
        lo, hi = int(m.group(1)) * 1000, int(m.group(2)) * 1000
        band += [v for v in (lo, hi) if 30_000 <= v <= 2_000_000]
    has_k = any((m.group(3) or "").lower() == "k" for m in _MONEY.finditer(text))
    for m in _MONEY.finditer(text):
        pre = text[max(0, m.start() - 4):m.start()].lower()
        if pre.endswith("401") or pre.endswith("401("):
            continue
        suf = (m.group(3) or "").lower()
        if suf in ("m", "b"):
            continue
        n = float(m.group(1).replace(",", ""))
        if suf == "k":
            n *= 1000
        elif n < 1000:
            if has_k:
                n *= 1000
            else:
                continue
        if 30_000 <= n <= 2_000_000:
            band.append(n)
    if not band:
        return None, None
    return int(min(band)), int(max(band))


def advisory_match(title: str, rules: dict) -> bool:
    """True if the title is one of the advisory/consulting roles that are
    allowed to bypass the location filter (on-site / travel accepted)."""
    t = (title or "").lower()
    return any(p.lower() in t for p in rules.get("advisory", []))


def title_ok(title: str, rules: dict) -> bool:
    t = (title or "").lower()
    # advisory titles count as includes, so a consulting role still matches even
    # if its exact words aren't in the main include list
    inc = [p.lower() for p in rules.get("include", [])] + \
          [p.lower() for p in rules.get("advisory", [])]
    exc = [p.lower() for p in rules.get("exclude", [])]
    if inc and not any(p in t for p in inc):
        return False
    return not any(p in t for p in exc)


def location_ok(location: str, description: str, rules: dict) -> bool:
    allow = [p.lower() for p in rules.get("allow", [])]
    if not allow:
        return True
    loc = (location or "").lower()
    if any(p in loc for p in allow):
        return True
    if rules.get("remote_ok"):
        blob = f"{loc} {(description or '')[:600].lower()}"
        if re.search(r"\bremote\b|work from home|distributed team", blob):
            return True
    return False


def filter_jobs(jobs: list, wl: dict) -> list:
    titles = wl.get("titles", {})
    loc_rules = wl.get("location", {})
    sal_rules = wl.get("salary", {})
    threshold = sal_rules.get("location_override_min", 200000)
    bypass_unlisted = sal_rules.get("bypass_location_if_unlisted", False)

    out = []
    for j in jobs:
        if not title_ok(j["title"], titles):
            continue

        lo, hi = parse_salary(f"{j['title']} {j.get('description', '')}")
        j["_salary"] = (lo, hi)

        loc_pass = location_ok(j["location"], j["description"], loc_rules)
        bypass = None
        if not loc_pass:
            # location fails normally — see if a rule waves it through
            if advisory_match(j["title"], titles):
                bypass = "advisory"
            elif hi is not None and hi >= threshold:
                bypass = f">=${threshold // 1000}K"
            elif (lo, hi) == (None, None) and bypass_unlisted:
                bypass = "salary n/l"
        j["_loc_bypass"] = bypass

        if loc_pass or bypass:
            out.append(j)
    return out


def salary_str(job: dict) -> str:
    """Compact salary tag for display, e.g. '$180-240K', '$95K', 'salary n/l'."""
    lo, hi = job.get("_salary", (None, None))
    if lo is None and hi is None:
        return "salary n/l"
    if lo == hi:
        return f"${lo // 1000}K"
    return f"${lo // 1000}-{hi // 1000}K"


def prescore(job: dict, wl: dict) -> int:
    """Cheap local relevance score (0-100) used to rank BEFORE any API call."""
    t = (job["title"] or "").lower()
    d = (job["description"] or "").lower()
    score = 50
    for p in wl.get("titles", {}).get("include", []):
        if p.lower() in t:
            score += 12
            break
    if re.search(r"\bremote\b", f"{job['location']} {d[:600]}".lower()):
        score += 12
    for term in ("financial analyst", "fp&a", "variance", "forecasting", "portfolio"):
        if term in t or term in d[:2500]:
            score += 4
    for term in ("bilingual", "spanish"):
        if term in d[:2500]:
            score += 3
    if re.search(r"\b(sql)\b", d[:3000]) and re.search(r"require", d[:3000]):
        score -= 6
    for term in ("senior", "sr.", " ii i", "lead"):
        if term in t:
            score -= 5
    return max(0, min(100, score))


# ----------------------------------------------------------------------------
# output
# ----------------------------------------------------------------------------
def as_job_text(job: dict) -> str:
    """Render a posting into the plain-text form intake/analyze expect."""
    head = f"{job['title']} - {job['company']}\n{job['location']}\n"
    if job.get("url"):
        head += f"Source: {job['url']}\n"
    return head + "\n" + (job.get("description") or "")


def print_matches(matches: list):
    if not matches:
        print("\nNo new matching postings.")
        return
    line = "=" * 72
    print(f"\n{line}\n  {len(matches)} NEW MATCHING POSTING(S)\n{line}")
    for i, j in enumerate(matches, 1):
        print(f"\n{i:2}. {j['title']}")
        print(f"    {j['company']}  |  {j['location'] or 'location n/s'}  "
              f"|  {salary_str(j)}  |  prescore {j['_prescore']}  |  {j['source']}")
        if j.get("_loc_bypass"):
            print(f"    [location filter bypassed: {j['_loc_bypass']}]")
        if j.get("url"):
            print(f"    {j['url']}")
        snippet = re.sub(r"\s+", " ", j.get("description", ""))[:180]
        if snippet:
            print(f"    {snippet}...")
    print(f"\n{line}")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def classify_company(entry: dict):
    """Fetch one board once and classify it, distinguishing a genuinely broken
    board from one that simply has no postings right now.

    Returns (status, count, error) where status is:
      OK      - the board resolved and returned >=1 posting
      EMPTY   - the board resolved fine but has 0 postings (NOT a problem)
      BROKEN  - the fetch errored (bad slug/ATS, 404/403, network) -> needs a fix
    """
    errs: list = []
    jobs = sources.fetch_company(entry, errs)
    if jobs:
        return "OK", len(jobs), None
    if errs:
        return "BROKEN", 0, errs[0].get("error")
    return "EMPTY", 0, None


def check_slugs(wl: dict):
    print("Checking watchlist slugs...\n")
    ok, empty, broken = [], [], []
    for entry in wl.get("companies", []):
        name = entry.get("name", entry.get("slug"))
        status, n, err = classify_company(entry)
        if status == "OK":
            ok.append(f"  OK      {name:24} {entry['ats']:16} {n} postings")
        elif status == "EMPTY":
            empty.append(f"  EMPTY   {name:24} {entry['ats']:16} resolves, 0 postings right now")
        else:
            broken.append(f"  BROKEN  {name:24} {entry['ats']:16} slug '{entry['slug']}' -> {err or 'no response'}")
    print("\n".join(ok + empty + broken))
    print(f"\n{len(ok)} OK, {len(empty)} empty (fine), {len(broken)} BROKEN.")
    if broken:
        print("Fix BROKEN boards: open the careers page, click a job, read the "
              "board domain in the address bar, correct ats/slug in watchlist.yaml.")


def health_report(wl: dict, dirname: str = "health"):
    """Write a dated watchlist health report. Meant to run weekly: BROKEN boards
    are surfaced loudly, EMPTY boards are listed quietly (not a problem), and OK
    boards are summarized. Returns the path written."""
    out = config.ROOT / dirname
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = out / f"{stamp}.md"

    ok, empty, broken = [], [], []
    for entry in wl.get("companies", []):
        status, n, err = classify_company(entry)
        row = {"name": entry.get("name", entry.get("slug")),
               "ats": entry.get("ats"), "slug": entry.get("slug"),
               "n": n, "err": err}
        {"OK": ok, "EMPTY": empty, "BROKEN": broken}[status].append(row)

    lines = [f"# Watchlist health — {stamp}", ""]
    total = len(ok) + len(empty) + len(broken)
    lines.append(f"**{total} boards checked — {len(ok)} OK, {len(empty)} empty, "
                 f"{len(broken)} BROKEN.**\n")

    if broken:
        lines.append("## \u26a0\ufe0f BROKEN — needs a fix")
        lines.append("These errored on fetch. Open the careers page, click a job, "
                     "read the board domain, and correct `ats`/`slug` in "
                     "`watchlist.yaml`.\n")
        for r in broken:
            lines.append(f"- **{r['name']}** ({r['ats']} / `{r['slug']}`) — {r['err'] or 'no response'}")
        lines.append("")
    else:
        lines.append("## \u2705 No broken boards\n")

    if empty:
        lines.append("## Empty (resolves, 0 postings right now — not a problem)")
        for r in empty:
            lines.append(f"- {r['name']} ({r['ats']} / `{r['slug']}`)")
        lines.append("")

    if ok:
        lines.append("## OK")
        for r in sorted(ok, key=lambda x: -x["n"]):
            lines.append(f"- {r['name']}: {r['n']} postings")
        lines.append("")

    path.write_text("\n".join(lines))
    return path, len(broken)



def write_digest(matches: list, dirname: str):
    """Write a dated markdown summary of the new matches (for scheduled runs)."""
    from datetime import datetime
    out = config.ROOT / dirname
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = out / f"{stamp}.md"
    lines = [f"# Job scan — {stamp}", ""]
    if not matches:
        lines.append("_No new matching postings._")
    else:
        lines.append(f"**{len(matches)} new matching posting(s):**\n")
        for j in matches:
            lines.append(f"### {j['_prescore']}  {j['title']} — {j['company']}")
            lines.append(f"- Location: {j['location'] or 'n/s'}  |  Pay: {salary_str(j)}  |  Source: {j['source']}")
            if j.get("_loc_bypass"):
                lines.append(f"- _Location filter bypassed: {j['_loc_bypass']}_")
            if j.get("url"):
                lines.append(f"- {j['url']}")
            snippet = re.sub(r"\s+", " ", j.get("description", ""))[:220]
            if snippet:
                lines.append(f"- {snippet}...")
            lines.append("")
    path.write_text("\n".join(lines))
    return path


def main():
    p = argparse.ArgumentParser(description="Poll company job boards for new matching roles")
    p.add_argument("--check", action="store_true", help="verify watchlist slugs resolve")
    p.add_argument("--health", nargs="?", const="health",
                   help="write a dated watchlist health report (default dir: health/); "
                        "surfaces BROKEN boards, ignores empty ones. Good weekly.")
    p.add_argument("--all", action="store_true", help="include already-seen postings")
    p.add_argument("--analyze", action="store_true", help="score matches with the analyzer")
    p.add_argument("--generate", action="store_true", help="with --analyze, build documents for 'apply' verdicts")
    p.add_argument("--top", type=int, default=8, help="how many to analyze (default 8)")
    p.add_argument("--save-dir", help="write each match to a .txt in this folder")
    p.add_argument("--digest", nargs="?", const="digests",
                   help="write a dated markdown digest of new matches (default dir: digests/)")
    p.add_argument("--json", action="store_true",
                   help="emit the run as one JSON object on stdout (for agents/scripts)")
    args = p.parse_args()

    if not WATCHLIST_PATH.exists():
        print(f"Missing {WATCHLIST_PATH}", file=sys.stderr)
        sys.exit(1)
    wl = yaml.safe_load(WATCHLIST_PATH.read_text())

    if args.json:
        import json as _json
        import agent_api
        print(_json.dumps(agent_api.scan_jobs(
            analyze=args.analyze, top=args.top,
            include_seen=args.all), indent=2))
        return

    if args.check:
        check_slugs(wl)
        return

    if args.health:
        path, n_broken = health_report(wl, args.health)
        print(f"Health report written to {path}")
        if n_broken:
            print(f"WARNING: {n_broken} board(s) BROKEN — see the report.")
        else:
            print("All boards healthy (no broken slugs).")
        return

    print(f"Polling {len(wl.get('companies', []))} company boards...")
    jobs = sources.fetch_watchlist(wl.get("companies", []))
    print(f"\nFetched {len(jobs)} total postings.")

    matches = filter_jobs(jobs, wl)
    print(f"{len(matches)} pass the title/location filters.")

    seen = load_seen()
    if not args.all:
        fresh = [j for j in matches if job_key(j) not in seen]
        print(f"{len(fresh)} are new since the last run.")
        matches = fresh

    for j in matches:
        j["_prescore"] = prescore(j, wl)
    matches.sort(key=lambda j: j["_prescore"], reverse=True)

    print_matches(matches)

    if args.digest:
        d = write_digest(matches, args.digest)
        print(f"Digest written to {d}")

    if args.save_dir:
        out = config.ROOT / args.save_dir
        out.mkdir(parents=True, exist_ok=True)
        for j in matches:
            slug = re.sub(r"[^a-z0-9]+", "_", f"{j['company']}_{j['title']}".lower()).strip("_")[:70]
            (out / f"{slug}.txt").write_text(as_job_text(j))
        print(f"Saved {len(matches)} posting(s) to {out}/")

    if args.analyze and matches:
        import anthropic
        import analyze as analyzer
        import tracker

        with open(config.PROFILE_PATH) as fh:
            profile = yaml.safe_load(fh)
        client = anthropic.Anthropic()

        print(f"\nAnalyzing top {min(args.top, len(matches))}...\n")
        for j in matches[:args.top]:
            a = analyzer.analyze(as_job_text(j), profile, client=client)
            kg = a.get("keyword_gap", {})
            print(f"  {a.get('fit_score')}/100  {a.get('recommendation','').upper():18} "
                  f"{j['title']} — {j['company']}  (kw {kg.get('coverage','?')}%)")
            if args.generate and a["recommendation"] != "skip":
                import generate
                from pipeline import keywords_from
                d = generate.build_both(profile, a["best_angle"], job=a,
                                        keywords=keywords_from(a))
                r = d["polished"]
                print(f"        resume -> {r}  (+ ATS twin: {d['ats'].name})")
                tracker.record(a, resume_file=r)
            else:
                tracker.record(a)

    # mark everything we showed as seen
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for j in matches:
        seen[job_key(j)] = {"title": j["title"], "company": j["company"], "first_seen": now}
    save_seen(seen)


if __name__ == "__main__":
    main()
