# Driving the pipeline from an AI agent

This pipeline was built to be run by a person *and* by an agent. The
human-facing entry points (`scan.py`, `pipeline.py`, `linkedin_inbox.py`,
`paste_batch.py`) print for a reader. The agent-facing twin is two files:

| File | What it gives your agent |
|------|--------------------------|
| `agent_api.py`  | Clean Python functions that return JSON-safe dicts and **never raise** |
| `agent_tools.py`| Anthropic tool schemas (`TOOLS`) + a `dispatch()` router, ready to hand to a model |

You can integrate at whichever level fits your agent:

1. **Import the functions** — if your agent is Python, `import agent_api` and
   call `agent_api.scan_jobs(...)` etc. directly.
2. **Tool-calling model** — pass `agent_tools.TOOLS` to the Claude API and route
   `tool_use` blocks through `agent_tools.dispatch()`. (Full loop below.)
3. **Shell out** — anything that can run a command can call `python scan.py
   --json` and parse stdout. Good for Claude Code or a non-Python runner.

---

## The tool surface

Nine tools, grouped by what the agent is doing:

- **discover** — `scan_jobs`, `read_linkedin_alerts`, `ingest_batch`
- **configure** — `list_watchlist`, `add_watchlist_company`, `check_watchlist`
- **triage** — `analyze_job`
- **produce** — `generate_documents`
- **track** — `list_applications`

Every one returns a dict with an `ok` boolean. On failure you get
`{"ok": false, "error": "..."}` instead of an exception — so a single bad call
never takes the agent down. `scan_jobs` additionally returns a structured
`errors` list that distinguishes an unreachable board or bad slug (HTTP 404/403)
from a board that simply has no matching roles right now — a distinction the raw
ATS APIs hide.

---

## Minimal tool-calling loop (Claude API)

This is the exact loop shape that's been tested against `dispatch()`:

```python
import json
import anthropic
import agent_tools

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
messages = [{"role": "user", "content": "Find me new roles worth applying to."}]

while True:
    resp = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=2048,
        tools=agent_tools.TOOLS,
        system=(
            "You manage a job-search pipeline for a finance professional. "
            "Use scan_jobs to find new roles, analyze_job to score ones the "
            "user shares, and generate_documents only after the user decides "
            "to apply. Be honest about weak fits."
        ),
        messages=messages,
    )
    messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})

    if resp.stop_reason != "tool_use":
        print("".join(b.text for b in resp.content if b.type == "text"))
        break

    results = []
    for b in resp.content:
        if b.type == "tool_use":
            out = agent_tools.dispatch(b.name, b.input)
            results.append(agent_tools.tool_result_block(b.id, out))
    messages.append({"role": "user", "content": results})
```

`tool_result_block(id, result)` just JSON-encodes the dict into the shape the
API expects; use it or build the block yourself.

---

## Windows notes

- **Paths just work.** Everything resolves paths relative to the package via
  `config.ROOT` (a `pathlib.Path`), so there are no hard-coded `/` paths to
  break on Windows. Run the agent from anywhere.
- **One environment, two secrets.** Set these for the current user so both the
  agent and any scheduled task see them:
  ```powershell
  setx ANTHROPIC_API_KEY "sk-ant-..."
  setx MAIL_HOST "imap.gmail.com"
  setx MAIL_USER "you@gmail.com"
  setx MAIL_PASS "your-app-password"
  ```
  (`setx` persists them; open a new terminal afterward.)
- **If the agent should run unattended**, point Task Scheduler at a script that
  launches it, the same way `run_scan.bat` launches the scan — see
  `SCHEDULING.md`. The agent and the plain scheduled scan can coexist: the scan
  keeps the watchlist warm, the agent reasons over what it finds.
- **venv activation** in a `.bat`: `call ".venv\Scripts\activate.bat"`.

---

## A sensible division of labor

You don't need the agent to do everything. A robust setup:

- **Scheduled `scan.py --digest`** (Task Scheduler, daily) keeps discovery
  running with zero tokens and leaves a dated digest.
- **The agent** is what you talk to: "anything good today?" → it calls
  `scan_jobs`, reasons over the ranked matches, pulls in `list_applications` to
  avoid repeats, and offers to `generate_documents` for the strong fits. It asks
  before generating and before adding companies.

Keep `generate_documents` gated behind an explicit user decision — it writes
files and logs an application, so it shouldn't fire on the agent's own
initiative.

---

## Quick reference — return shapes

```
scan_jobs           -> {ok, polled, fetched, matched, new, jobs:[...], errors:[...]}
read_linkedin_alerts-> {ok, emails_read, jobs:[...]}
ingest_batch        -> {ok, count, results:[...]}
list_watchlist      -> {ok, companies:[...], title_include, title_exclude, locations}
add_watchlist_company-> {ok, added:{name,ats,slug}, total_companies}
check_watchlist     -> {ok, resolved:[...], unresolved:[...]}
analyze_job         -> {ok, fit_score, recommendation, reasons, dealbreakers,
                        keyword_coverage, keyword_priority_missing, ...}
generate_documents  -> {ok, resume, cover_letter, ats_lint, fit_score, recommendation}
list_applications   -> {ok, count, applications:[...]}
```

---

## Local server (call the pipeline over HTTP)

`server.py` exposes every `agent_api` function as a localhost HTTP endpoint, so
a browser extension, bookmarklet, agentic browser, or plain `curl` can drive the
pipeline without importing Python.

```bash
pip install -r requirements.txt
python server.py          # http://127.0.0.1:8765
```

| Method + path | Function |
|---------------|----------|
| `GET  /health` | liveness |
| `POST /scan` | scan_jobs `{analyze?, top?, include_seen?}` |
| `POST /analyze` | analyze_job `{job_text}` |
| `POST /generate` | generate_documents `{job_text}` |
| `POST /ingest` | ingest_batch `{text, generate?}` |
| `GET  /watchlist` / `POST /watchlist` | list / add company |
| `GET  /watchlist/check` | check_watchlist |
| `POST /linkedin` | read_linkedin_alerts `{days?}` |
| `GET  /applications` | list_applications `?status=` |

It binds to 127.0.0.1 only — nothing off your machine can reach it. Cost is
zero for the server itself; analyze/generate incur the same Claude API usage the
CLI does.
