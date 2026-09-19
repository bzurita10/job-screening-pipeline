"""sources.py — pull job postings from PUBLIC, no-auth ATS job-board APIs.

Most employers publish openings through Greenhouse, Lever, Ashby, Workable or
SmartRecruiters, and each exposes a documented public JSON endpoint for its own
board. Those are designed to be read by aggregators — no scraping, no login, no
ToS problem. This module fetches them, normalizes the results into one schema,
and hands them to the existing analyzer.

Important design constraint: there is NO global search endpoint for any of these
providers. Every call is scoped to one company's board token. So "scan
Greenhouse" really means "fetch N company boards and filter locally" — which is
exactly why this module is driven by a watchlist of companies (watchlist.yaml).

Endpoints used (all public, no auth):
    Greenhouse     https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    Lever          https://api.lever.co/v0/postings/{slug}?mode=json
    Ashby          https://api.ashbyhq.com/posting-api/job-board/{slug}
    Workable       https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true
    SmartRecruiters https://api.smartrecruiters.com/v1/companies/{slug}/postings

Finding a company's slug: open its careers page. If the URL (or the embedded
iframe) contains boards.greenhouse.io/acme, jobs.lever.co/acme, or
jobs.ashbyhq.com/acme, then "acme" is the slug.

Note on Workday: Workday has no public job API and renders via JavaScript, so
Workday-hosted boards can't be polled this way — those stay manual (see
linkedin_inbox.py / batch paste).
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

USER_AGENT = "job-screening-pipeline/1.0 (personal job search)"
TIMEOUT = 25
RETRIES = 3
PAUSE = 1.0   # be polite: these boards throttle aggressive callers


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def _get_json(url: str, errors=None):
    """GET a URL and parse JSON, with retry/backoff.

    Returns the parsed object on success, or None on failure. When `errors` is
    a list, a structured {"url", "error", "status"} dict is appended on failure
    instead of printing — this lets a caller (an agent) tell a bad slug (404)
    apart from a board that simply has no roles right now (200 + empty).
    """
    last = None
    status = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            status = e.code
            last = f"HTTP {e.code}"
            if e.code in (404, 401, 403):
                break            # bad slug or blocked — don't hammer
        except Exception as e:    # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(PAUSE * (attempt + 1))
    if errors is not None:
        errors.append({"url": url, "error": last, "status": status})
    else:
        print(f"    ! fetch failed ({last}): {url}")
    return None


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _job(company, title, location, url, description="", posted="", source=""):
    return {
        "company": company, "title": title or "", "location": location or "",
        "url": url or "", "description": description or "",
        "posted": posted or "", "source": source,
    }


# ----------------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------------
def from_greenhouse(slug, company=None, errors=None):
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", errors)
    if not data:
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(_job(
            company or slug,
            j.get("title"),
            (j.get("location") or {}).get("name", ""),
            j.get("absolute_url"),
            _strip_html(j.get("content", "")),
            j.get("updated_at", ""),
            "greenhouse",
        ))
    return out


def from_lever(slug, company=None, errors=None):
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json", errors)
    if not data:
        return []
    out = []
    for j in data:
        cat = j.get("categories") or {}
        body = j.get("descriptionPlain") or _strip_html(j.get("description", ""))
        lists = " ".join(
            f"{s.get('text','')}: {_strip_html(s.get('content',''))}"
            for s in (j.get("lists") or [])
        )
        out.append(_job(
            company or slug, j.get("text"), cat.get("location", ""),
            j.get("hostedUrl"), (body + "\n" + lists).strip(),
            j.get("createdAt", ""), "lever",
        ))
    return out


def from_ashby(slug, company=None, errors=None):
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", errors)
    if not data:
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(_job(
            company or slug, j.get("title"), j.get("location", ""),
            j.get("jobUrl"),
            _strip_html(j.get("descriptionHtml") or j.get("descriptionPlain", "")),
            j.get("publishedAt", ""), "ashby",
        ))
    return out


def from_workable(slug, company=None, errors=None):
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", errors)
    if not data:
        return []
    out = []
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        out.append(_job(
            company or slug, j.get("title"), loc, j.get("url") or j.get("shortlink"),
            _strip_html(j.get("description", "")), j.get("published_on", ""), "workable",
        ))
    return out


def from_smartrecruiters(slug, company=None, errors=None):
    data = _get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100", errors)
    if not data:
        return []
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        where = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
        out.append(_job(
            company or slug, j.get("name"), where,
            f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
            "", j.get("releasedDate", ""), "smartrecruiters",
        ))
    return out


def _rippling_location(j: dict) -> str:
    """Rippling exposes location in a few shapes across boards — string, dict,
    or a list of either. Flatten whatever is there to a readable string."""
    v = (j.get("workLocation") or j.get("location") or j.get("locations")
         or j.get("workplaceCity") or "")
    def one(x):
        if isinstance(x, str):
            return x
        if isinstance(x, dict):
            return ", ".join(str(x[k]) for k in ("city", "state", "region", "country", "name")
                             if x.get(k))
        return ""
    if isinstance(v, list):
        return " / ".join(p for p in (one(x) for x in v) if p)
    return one(v)


def from_rippling(slug, company=None, errors=None):
    """Rippling's own ATS (companies whose jobs live on ats.rippling.com).
    Public board endpoint returns a thin list (no descriptions) and repeats a
    job once per work location, so we dedupe by uuid. Field names vary by board,
    so each is read defensively."""
    data = _get_json(f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs", errors)
    if not data:
        return []
    rows = data if isinstance(data, list) else (
        data.get("jobs") or data.get("data") or data.get("results")
        or data.get("items") or data.get("postings") or [])
    seen, out = set(), []
    for j in rows:
        if not isinstance(j, dict):
            continue
        uid = j.get("uuid") or j.get("id") or j.get("jobId") or j.get("job_id")
        if uid:
            if uid in seen:
                continue         # same role, another location — already captured
            seen.add(uid)
        title = j.get("name") or j.get("title") or j.get("jobTitle") or j.get("job_title")
        url = (j.get("url") or j.get("jobUrl") or j.get("applyUrl") or j.get("apply_url")
               or (f"https://ats.rippling.com/{slug}/jobs/{uid}" if uid else ""))
        out.append(_job(company or slug, title, _rippling_location(j), url, "", "", "rippling"))
    return out


PROVIDERS = {
    "greenhouse": from_greenhouse,
    "lever": from_lever,
    "ashby": from_ashby,
    "workable": from_workable,
    "smartrecruiters": from_smartrecruiters,
    "rippling": from_rippling,
}


def fetch_company(entry: dict, errors=None):
    """entry: {name, ats, slug}. Returns a list of normalized job dicts."""
    fn = PROVIDERS.get((entry.get("ats") or "").lower())
    if not fn:
        print(f"    ! unknown ats '{entry.get('ats')}' for {entry.get('name')}")
        return []
    jobs = fn(entry["slug"], entry.get("name"), errors)
    time.sleep(PAUSE)
    return jobs


def fetch_watchlist(companies: list, errors=None):
    """Fetch every company in the watchlist. Returns a flat list of jobs."""
    all_jobs = []
    for entry in companies:
        name = entry.get("name", entry.get("slug"))
        print(f"  - {name} ({entry.get('ats')})...", end=" ", flush=True)
        jobs = fetch_company(entry, errors)
        print(f"{len(jobs)} postings")
        all_jobs.extend(jobs)
    return all_jobs
