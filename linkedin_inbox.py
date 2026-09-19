#!/usr/bin/env python3
"""linkedin_inbox.py — turn LinkedIn job-alert EMAILS into pipeline input.

WHY THIS EXISTS
    LinkedIn's Terms of Service prohibit scraping, and they actively block
    automated access. There is no compliant way to crawl LinkedIn job search.
    But LinkedIn will happily *send you* matching jobs: saved searches email you
    new postings on a schedule. Reading your own inbox is an ordinary, permitted
    use of mail you received — so this turns LinkedIn from a manual,
    screenshot-driven source into a hands-off feed, without touching their site.

SETUP (one time)
  1. On LinkedIn, run the job search you care about (title + location + Remote
     filter), then toggle "Set alert" on. Do this for each search you want —
     e.g. "Financial Analyst, Remote, United States", "FP&A Analyst, Remote".
     Set frequency to Daily.
  2. Make sure the alerts land somewhere this script can read:
       - Gmail: create an app password (Google Account > Security > 2-Step
         Verification > App passwords) and use IMAP.
       - Or set up a filter so alerts go to a dedicated label/folder.
  3. Export credentials before running:
       export MAIL_HOST=imap.gmail.com
       export MAIL_USER=you@gmail.com
       export MAIL_PASS=your-app-password
       export MAIL_FOLDER=INBOX          # or "Job Alerts"

USAGE
    python linkedin_inbox.py                 # parse recent alerts, list roles
    python linkedin_inbox.py --days 3
    python linkedin_inbox.py --save-dir jobs/inbox
    python linkedin_inbox.py --analyze --top 5

WHAT YOU GET
    LinkedIn alert emails contain the job title, company, location and a link
    per posting. That's enough to triage and rank. The email does NOT contain
    the full description, so for the ones you want to pursue, this prints the
    links — fetch the description with `pipeline.py --url <link>` where the page
    is public, or paste it into `paste_batch.py`. Either way you're no longer
    hand-collecting every posting: you triage a ranked list and only touch the
    few worth pursuing.
"""

import argparse
import email
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from html import unescape
from urllib.parse import urlparse, urlunparse

import config

SENDERS = ["jobs-listings@linkedin.com", "jobalerts-noreply@linkedin.com",
           "jobs-noreply@linkedin.com", "noreply@linkedin.com"]


def _decode(raw):
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001
        return raw or ""


def _html_of(msg):
    """Extract the text/html part (LinkedIn alerts are HTML)."""
    if msg.is_multipart():
        html = ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                html += payload.decode(part.get_content_charset() or "utf-8", "replace")
        return html
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


def clean_url(url: str) -> str:
    """Strip LinkedIn's tracking querystring down to the canonical job URL."""
    url = unescape(url)
    m = re.search(r"/jobs/view/(\d+)", url)
    if m:
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def parse_alert(html: str):
    """Pull (title, company, location, url) tuples out of one alert email.

    LinkedIn's alert markup varies, so rather than relying on a fixed table
    shape we take the text that FOLLOWS each job link, split it into lines at
    every tag boundary, and classify: the first non-empty line is the company,
    and the first line that looks like a place (contains a state code, a
    country, or Remote/Hybrid/On-site) is the location.
    """
    jobs, seen = [], set()
    LOC_RE = re.compile(
        r"(remote|hybrid|on-?site|,\s*[A-Z]{2}\b|United States|Area|Metropolitan|"
        r"Anywhere|[A-Z][a-z]+,\s*[A-Z][a-z]+)", re.I)

    blocks = re.finditer(
        r'<a[^>]+href="([^"]*?/jobs/view/\d+[^"]*)"[^>]*>(.*?)</a>(.{0,800}?)'
        r'(?=<a[^>]+href="[^"]*?/jobs/view/\d+|\Z)',
        html, re.S | re.I)

    for m in blocks:
        url_raw, title_html, tail = m.groups()
        title = re.sub(r"\s+", " ",
                       unescape(re.sub(r"<[^>]+>", " ", title_html))).strip()
        if not title or len(title) < 3:
            continue

        # every tag becomes a line break, so each cell lands on its own line
        tail_lines = unescape(re.sub(r"<[^>]+>", "\n", tail))
        parts = [re.sub(r"\s+", " ", s).strip() for s in tail_lines.split("\n")]
        parts = [s for s in parts if s and s.lower() != title.lower() and len(s) > 1]

        company, location = "", ""
        for s in parts:
            if not location and LOC_RE.search(s):
                location = s
                continue
            if not company:
                company = s
            if company and location:
                break

        url = clean_url(url_raw)
        if url in seen:
            continue
        seen.add(url)
        jobs.append({"title": title, "company": company,
                     "location": location, "url": url, "source": "linkedin-alert"})
    return jobs


def fetch(days: int):
    host = os.environ.get("MAIL_HOST")
    user = os.environ.get("MAIL_USER")
    password = os.environ.get("MAIL_PASS")
    folder = os.environ.get("MAIL_FOLDER", "INBOX")
    if not all([host, user, password]):
        print("Set MAIL_HOST, MAIL_USER and MAIL_PASS first (see the docstring).",
              file=sys.stderr)
        sys.exit(1)

    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    M = imaplib.IMAP4_SSL(host)
    M.login(user, password)
    M.select(f'"{folder}"')

    uids = []
    for sender in SENDERS:
        typ, data = M.search(None, f'(SINCE {since} FROM "{sender}")')
        if typ == "OK":
            uids.extend(data[0].split())
    # de-dupe while preserving order
    uids = list(dict.fromkeys(uids))

    all_jobs, seen_urls = [], set()
    for uid in uids:
        typ, data = M.fetch(uid, "(RFC822)")
        if typ != "OK":
            continue
        msg = email.message_from_bytes(data[0][1])
        for j in parse_alert(_html_of(msg)):
            if j["url"] in seen_urls:
                continue
            seen_urls.add(j["url"])
            j["alert"] = _decode(msg.get("Subject", ""))
            all_jobs.append(j)
    M.logout()
    return all_jobs, len(uids)


def main():
    p = argparse.ArgumentParser(description="Parse LinkedIn job-alert emails")
    p.add_argument("--days", type=int, default=7, help="how far back to read (default 7)")
    p.add_argument("--save-dir", help="write a stub .txt per job for later enrichment")
    p.add_argument("--analyze", action="store_true",
                   help="fetch each public posting and score it (uses the API)")
    p.add_argument("--top", type=int, default=5)
    args = p.parse_args()

    jobs, n_mail = fetch(args.days)
    print(f"Read {n_mail} alert email(s); found {len(jobs)} unique posting(s).\n")
    line = "=" * 72
    print(line)
    for i, j in enumerate(jobs, 1):
        print(f"{i:2}. {j['title']}")
        print(f"    {j['company']}  |  {j['location'] or 'location n/s'}")
        print(f"    {j['url']}")
    print(line)

    if args.save_dir:
        out = config.ROOT / args.save_dir
        out.mkdir(parents=True, exist_ok=True)
        for j in jobs:
            slug = re.sub(r"[^a-z0-9]+", "_", f"{j['company']}_{j['title']}".lower()).strip("_")[:70]
            (out / f"li_{slug}.txt").write_text(
                f"{j['title']} - {j['company']}\n{j['location']}\nSource: {j['url']}\n\n"
                "[Description not included in the alert email. Paste the full text "
                "below this line, then run: python pipeline.py <this file>]\n")
        print(f"\nSaved {len(jobs)} stub(s) to {out}/ — paste descriptions in, then run batch.py.")

    if args.analyze and jobs:
        import anthropic, yaml
        import analyze as analyzer
        import intake
        with open(config.PROFILE_PATH) as fh:
            profile = yaml.safe_load(fh)
        client = anthropic.Anthropic()
        print(f"\nAnalyzing top {min(args.top, len(jobs))} (public pages only)...\n")
        for j in jobs[:args.top]:
            try:
                text = intake.from_url(j["url"])
            except Exception as e:  # noqa: BLE001
                print(f"  -- {j['title']} — {j['company']}: could not fetch ({e}); paste manually")
                continue
            if not text or len(text) < 400:
                print(f"  -- {j['title']} — {j['company']}: page is JS-only; paste manually")
                continue
            a = analyzer.analyze(text, profile, client=client)
            kg = a.get("keyword_gap", {})
            print(f"  {a.get('fit_score')}/100  {a.get('recommendation','').upper():18} "
                  f"{j['title']} — {j['company']}  (kw {kg.get('coverage','?')}%)")


if __name__ == "__main__":
    main()
