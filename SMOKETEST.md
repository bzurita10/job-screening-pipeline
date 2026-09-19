# Smoke test — verify a fresh install in ~5 minutes

Run these in order after `setup.bat` (or a manual install). Four of the five
steps make **no API call and cost nothing**; only Step 3 (the full pipeline)
uses the Claude API, at roughly 3–4 cents. Commands assume Windows / PowerShell
(`python`, backslash paths); on macOS/Linux use `python3` and forward slashes.

Prerequisite: dependencies installed and `profile.yaml` contacts filled in.
`ANTHROPIC_API_KEY` is only needed for Step 3.

---

## 1. Boards resolve — `--check`  (free, network)

```
python scan.py --check
```

**Pass:** every company prints `OK` with a live posting count.
**Watch for:** any `BAD` line means that slug/ATS didn't resolve. The two most
likely this round are **Deel** and **Rippling** (slugs not confirmed from a live
URL when they were added). If one is `BAD`, open the company's careers page,
click a job, read the board domain in the address bar, and fix `ats`/`slug` in
`watchlist.yaml` — then re-run `--check`.

## 2. Scanner runs — new matches  (free, network)

```
python scan.py
```

**Pass:** it polls every board and prints a ranked shortlist (or "No new
matching postings" if nothing new — also a pass; run `python scan.py --all` to
see matches regardless of seen-state).
**Look for:** salary tags (`$180-240K`, `salary n/l`) and any
`[location filter bypassed: ...]` notes — that confirms the new salary/advisory
logic is live.

## 3. Full pipeline on a sample role  (~3–4 cents, uses API)

```
python pipeline.py jobs\greenhouse_fpa.txt
```

**Pass:** prints an analysis (fit score + keyword gap), then writes **two**
resumes to `output\` — a polished one and an `_ATS` twin — plus a cover letter,
and lints both.
**Expected lint note:** every check should PASS. If "Contact in body" FAILs,
your `profile.yaml` still has placeholder contacts — fill them and re-run.
**No-cost variant:** add `--dry-run` to analyze only (still one API call, no
documents), or skip this step entirely if you just want the free checks.

## 4. Agent server up — `/health`  (free)

In one terminal:

```
python server.py
```

In a second terminal (or a browser):

```
curl http://127.0.0.1:8765/health
```

**Pass:** returns JSON like `{"ok": true, ...}`. The server binds to
`127.0.0.1` only, so it's not exposed to your network.

## 5. Stop the server cleanly  (free)

Press **Ctrl+C** in the server's terminal. To confirm it's down:

```
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue
```

**Pass:** no output = the port is free and the server is stopped.
(`Get-Process python | Stop-Process` erroring "cannot find process" just means
it's already stopped — that's fine.)

---

### If all five pass
The install is healthy end to end: discovery (1–2), analysis + document
generation (3), and the agent layer (4–5). From here the normal loop is:

```
python scan.py                     # what's new
python scan.py --analyze --top 5   # score the best (uses API)
python pipeline.py <a good match>  # full package for the one you want
```
