# AI-Powered Job-Screening & Resume Automation Pipeline

A Python system that automates the tedious parts of a finance job search: it
**researches a posting, scores how well it fits a structured candidate profile,
flags dealbreakers, and generates a tailored resume and cover letter** — turning
~30 minutes of per-application work into under 5.

Built around the Anthropic Claude API, with a clean separation between the parts
that need an LLM (reading a posting, scoring fit, drafting prose) and the parts
that shouldn't (assembling a resume from verified facts).

---

## Why this exists

Applying to finance roles is repetitive: read the posting, judge whether it's
worth it, re-tailor a resume to the right angle, write a cover letter, log it.
This pipeline does the judging and tailoring automatically and consistently,
encoding the candidate's real filters (salary floor, remote preference,
skill gaps) and known low-quality-posting patterns so the scoring reflects
actual priorities rather than keyword overlap.

## What it does

1. **Intake** — a job description from a file, a paste, a public URL, or a
   **web search** (give it a title and it finds the posting).
2. **Analyze** — one Claude API call extracts title, company, salary, and
   requirements; screens against the candidate's filters and red-flag patterns;
   picks the best-fitting resume angle; and returns a 0–100 fit score with
   reasons and any dealbreakers.
3. **Generate** — a tailored resume (built deterministically from the profile,
   so it never invents experience) plus an AI-drafted cover letter.
4. **ATS keyword gap** (`ats_keywords.py`) — deterministically compares the
   posting's keywords to the profile and reports coverage %, matched terms, and
   the missing terms to add (with the ones named in *hard requirements* flagged
   as priority). Runs inside `analyze()` with no extra API call.
5. **ATS lint** (`ats_lint.py`) — scans the generated `.docx` for the formatting
   that breaks real parsers (tables, columns, images, text boxes, header/footer
   contact info, non-standard headings, unparseable dates, unsafe fonts) and
   returns PASS / WARN / FAIL. The pipeline lints every resume it builds.
6. **Track** — every screened role is logged to a CSV.

Both ATS checks encode how the dominant systems (Workday, Greenhouse, iCIMS,
Taleo, Lever) actually filter: **stage 1** parses the resume into fields — bad
formatting scrambles them — and **stage 2** scores keyword overlap and applies
knockouts. The lint protects stage 1; the keyword gap targets stage 2.

Both are standalone-runnable:

```bash
python ats_keywords.py jobs/greenhouse_fpa.txt      # keyword gap vs your profile
python ats_lint.py output/Resume_ATS_FA.docx        # format audit -> PASS/WARN/FAIL
```

Two batch modes wrap the core: **`batch.py`** over a folder of saved
descriptions, and **`research.py`** over a plain list of *titles*.

## Finding the jobs (the front end)

The pipeline used to start with you pasting a link or a screenshot. These four
sources remove most of that work.

**1. Company watchlist poller — `scan.py` + `watchlist.yaml`**
Most employers publish openings through Greenhouse, Lever, Ashby, Workable or
SmartRecruiters, each of which exposes a **public, documented, no-auth JSON
endpoint** for its own board. `scan.py` polls every company in your watchlist,
filters by your title and location rules, drops anything it has already shown
you, and prints a ranked shortlist — then optionally scores the survivors with
the analyzer.

```bash
python scan.py --check                  # verify your watchlist slugs resolve
python scan.py                          # what's new since last run
python scan.py --analyze --top 5        # score the best 5
python scan.py --analyze --generate     # ...and build resumes for them
```

Run it daily and it only ever shows you what's new (it remembers what it has
already surfaced in `data/seen_jobs.json`). `--digest` drops a dated
`digests/YYYY-MM-DD.md` you can skim each morning. To automate it, see
**SCHEDULING.md** — cron on macOS/Linux, Task Scheduler on Windows, using the
included `run_scan.sh` / `run_scan.bat` wrappers.

The bundled `watchlist.yaml` ships with several wealth/advisory and fintech
boards already filled in — Addepar, Savvy Wealth, Facet, Carta among them
(marked VERIFIED where the board URL was confirmed live). Run `python scan.py
--check` once to confirm they still resolve before relying on them.

There is no global search endpoint on any of these providers — every call is
scoped to one company's board token — which is exactly why this is driven by a
company watchlist rather than a keyword search. Add a company by finding its
slug in its careers URL (`boards.greenhouse.io/ACME` → `slug: acme`).

**2. LinkedIn job alerts — `linkedin_inbox.py`**
LinkedIn's ToS prohibits scraping and they actively block bots, so there is no
compliant crawler. Instead, let LinkedIn push to you: save your searches as
**job alerts**, and this reads those alert emails over IMAP, extracts every
posting (title, company, location, clean URL), de-dupes, and ranks them.
Reading your own inbox is an ordinary use of mail you received.

```bash
export MAIL_HOST=imap.gmail.com MAIL_USER=you@gmail.com MAIL_PASS=app-password
python linkedin_inbox.py --days 3
python linkedin_inbox.py --save-dir jobs/inbox
```

**3. Batch paste — `paste_batch.py`**
For anything the pollers can't reach (LinkedIn postings, Workday boards).
Paste several descriptions in one blob separated by `---`; it splits, scores
and ranks them in one pass, so you stop doing one screenshot per job.

## Driving it from an AI agent

There's also a thin local HTTP server (`server.py`, `python server.py` on `127.0.0.1:8765`) that exposes every function as a JSON endpoint for extensions, bookmarklets, or curl — see AGENT.md.


Every capability here is exposed as clean, JSON-returning functions (`agent_api.py`) and as Anthropic tool schemas with a dispatcher (`agent_tools.py`), so you can hand the whole pipeline to a tool-calling model in one import, or shell out with `python scan.py --json`. See **AGENT.md** for the tool list, a tested tool-calling loop, and Windows setup.

**4. Aggregator APIs (optional)**
Indeed and ZipRecruiter both run publisher/partner API programs, and Adzuna
offers an open job-board API. These are the licensed way to query aggregator
inventory; each needs an approved key, so they're left as a documented option
rather than wired in.

> **Workday note:** Workday has no public job API and renders via JavaScript,
> so Workday-hosted boards can't be polled — route those through LinkedIn
> alerts or batch paste.

## Quickstart

```bash
pip install -r requirements.txt
cp profile.example.yaml profile.yaml     # then add your details
export ANTHROPIC_API_KEY=sk-ant-...

python pipeline.py --paste                              # screen one role
python pipeline.py --search "FP&A Analyst, Acme, Remote"
python research.py roles.txt                            # a list of titles -> ranked shortlist + resumes
```

**Windows, one command:** double-click **`setup.bat`** (or run it from a
terminal) to install dependencies, check your API key, flag unfilled contacts,
and verify the watchlist boards resolve. Then follow **`SMOKETEST.md`** for a
~5-minute end-to-end check.

Generated documents land in `output/`; the tracker is `data/applications.csv`.
Both are git-ignored — your personal data stays local.

## Walkthrough

**Screen one role** — the pipeline finds the posting, scores it, and writes a
tailored resume + cover letter:

```text
$ python pipeline.py --search "FP&A Analyst, Greenhouse, Remote"

==============================================================
  FP&A Analyst  —  Greenhouse
==============================================================
  Location   : Anywhere in the United States  (remote)
  Salary     : $79,300 – $97,200
  Fit score  : 78/100
  Best angle : FA
  Verdict    : APPLY
  Why it fits:
    + Forecasting, variance, and reporting experience maps to the core
    + Advanced Excel + Bloomberg + Python cover the modeling requirement
    + Documented AI-tool use answers the "experiment with AI tools" ask
  Gaps:
    - Buy-side/wealth background, not corporate FP&A — needs framing
==============================================================

Resume  -> output/Resume_FA_greenhouse.docx
Cover   -> output/CoverLetter_greenhouse_fp_a_analyst.docx
Tracker -> data/applications.csv
```

**Research a whole list of titles** — one command turns role names into a
ranked shortlist, generating documents only for the strong matches (skips sink
to the bottom, dealbreakers force a skip):

```text
$ python research.py roles.txt

Researching 5 role(s) via web search ...
  [1/5] FP&A Analyst, Greenhouse ................ 78/100 -> apply
  [2/5] Client Success Manager, WealthTech ...... 88/100 -> apply
  [3/5] Pricing Analyst, Disney ................. 68/100 -> apply-with-caution
  [4/5] Sr Operations Integration PM, Disney .... 38/100 -> skip
  [5/5] Financial Data Analyst, on-site ......... 22/100 -> skip

------------------------------------------------------------------------------
 #  SCORE  VERDICT              COMPANY               TITLE               ANGLE
------------------------------------------------------------------------------
 1     88  apply                WealthTech Platform   Client Success Mgr  CSA
 2     78  apply                Greenhouse            FP&A Analyst        FA
 3     68  apply-with-caution   Disney (WDW)          Pricing Analyst     FA
 4     38  skip                 Disney Cruise Line    Sr Ops Integration  BA
 5     22  skip                 (unnamed)             Financial Data Anl  BA
------------------------------------------------------------------------------
Shortlist -> output/shortlist.csv

Generating documents for 2 role(s):
  + WealthTech Platform — Client Success Manager
  + Greenhouse — FP&A Analyst
```

The dealbreaker logic is the point: role #5 requires hard SQL and is on-site
below the salary floor, so it scores low and is skipped automatically — the
pipeline enforces the candidate's real filters instead of just matching keywords.

## Architecture

| Module | Responsibility |
|---|---|
| `profile.yaml` | Single source of truth: experience, tagged bullets, six resume angles, filters, red-flag patterns |
| `intake.py` | Load a posting from file / paste / URL / web search |
| `analyze.py` | LLM extraction + fit scoring against the profile (strict-JSON output) |
| `generate.py` | Deterministic resume + AI cover letter as formatted `.docx` |
| `tracker.py` | Append each screened role to a CSV |
| `pipeline.py` | Single-role CLI orchestrator |
| `batch.py` / `research.py` | Batch over a folder / over a list of titles |

## Design decisions

- **No LinkedIn scraper.** Scraping LinkedIn violates its ToS and breaks
  constantly. Intake takes a paste, a public URL, or a web search instead —
  more robust and not a liability.
- **Resume generation is deterministic; only scoring and cover letters use the
  LLM.** The resume is assembled from verified facts in `profile.yaml`, so it
  can't hallucinate experience — and it runs offline and free. The LLM is used
  where judgment and prose actually matter.
- **Filters and red-flag patterns live in the profile, not the code.** Salary
  floor, remote preference, and skill-gap flags are data, so the scoring adapts
  to the candidate without code changes.

## Tech

Python · Anthropic Claude API · prompt engineering · structured JSON extraction ·
web-search tooling · python-docx · PyYAML

## License

MIT — see [LICENSE](LICENSE).
