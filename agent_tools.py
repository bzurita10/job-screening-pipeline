"""agent_tools.py — Anthropic tool-use definitions for the pipeline.

Drop-in wiring for a tool-calling agent. Two exports:

    TOOLS     the list you pass to client.messages.create(tools=TOOLS)
    dispatch  dispatch(name, tool_input) -> dict, runs the matching function

Every tool maps 1:1 to an agent_api function and returns a JSON-safe dict, so
the agent loop is just: send TOOLS, on a tool_use block call dispatch(), feed
the dict back as a tool_result, repeat. See AGENT.md for a full working loop.
"""

import json

import agent_api

TOOLS = [
    {
        "name": "scan_jobs",
        "description": (
            "Poll every company board in the watchlist, filter by the saved "
            "title/location rules, drop postings already seen on earlier runs, "
            "rank what's new, and return them. Set analyze=true to also score "
            "the top matches for fit (uses the Claude API). This is the main "
            "discovery action — call it to find new roles to consider."),
        "input_schema": {
            "type": "object",
            "properties": {
                "analyze": {"type": "boolean",
                            "description": "Also score the top matches for fit and keyword coverage. Default false."},
                "top": {"type": "integer",
                        "description": "How many matches to score when analyze=true. Default 8."},
                "include_seen": {"type": "boolean",
                                 "description": "Include postings surfaced on previous runs. Default false."},
            },
        },
    },
    {
        "name": "read_linkedin_alerts",
        "description": (
            "Read LinkedIn job-alert emails over IMAP and return the postings "
            "(title, company, location, link). Requires MAIL_HOST/USER/PASS in "
            "the environment. Use for the user's highest-priority source when "
            "they've set up LinkedIn saved-search alerts."),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer",
                         "description": "How many days back to read. Default 7."},
            },
        },
    },
    {
        "name": "ingest_batch",
        "description": (
            "Take a blob of one or more job descriptions pasted by the user "
            "(separated by a line of ---), split them, score each for fit, and "
            "return a ranked list. Use for postings the pollers can't reach, "
            "like LinkedIn or Workday pages. Set generate=true to also build "
            "resumes for non-skip matches."),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The pasted posting(s)."},
                "generate": {"type": "boolean",
                             "description": "Build documents for good matches. Default false."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "list_watchlist",
        "description": "Return the companies and the title/location filters currently in the watchlist.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_watchlist_company",
        "description": (
            "Add a company to the watchlist so future scans include it. You "
            "need its ATS platform (greenhouse, lever, ashby, workable, or "
            "smartrecruiters) and its board slug, both read from the company's "
            "careers-page URL. Run check_watchlist afterward to confirm it "
            "resolves."),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Display name, e.g. 'Farther'."},
                "ats": {"type": "string",
                        "enum": ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"],
                        "description": "The ATS platform hosting the board."},
                "slug": {"type": "string", "description": "The board slug from the careers URL."},
            },
            "required": ["name", "ats", "slug"],
        },
    },
    {
        "name": "check_watchlist",
        "description": (
            "Fetch every watchlist board once and report which slugs resolve "
            "and which return nothing (a bad slug vs an empty board). Run this "
            "after editing the watchlist or when a company stops appearing."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "analyze_job",
        "description": (
            "Score a single job description against the user's profile: fit "
            "score 0-100, apply/caution/skip recommendation, reasons, "
            "dealbreakers, and ATS keyword-gap coverage. Use when the user "
            "shares one specific posting."),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_text": {"type": "string", "description": "The full job description text."},
            },
            "required": ["job_text"],
        },
    },
    {
        "name": "generate_documents",
        "description": (
            "Build a tailored, ATS-safe resume and a cover letter for one "
            "posting, lint the resume for parser-breaking formatting, and log "
            "it to the application tracker. Returns file paths. Use once the "
            "user decides to apply."),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_text": {"type": "string", "description": "The full job description text."},
            },
            "required": ["job_text"],
        },
    },
    {
        "name": "list_applications",
        "description": (
            "Read the application tracker and return logged roles (date, "
            "status, company, title, fit, files). Optionally filter by status. "
            "Use to answer 'what have I applied to' / 'what's in progress'."),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string",
                           "description": "Optional status filter, e.g. 'screened' or 'applied'."},
            },
        },
    },
]

# name -> callable
_REGISTRY = {
    "scan_jobs": agent_api.scan_jobs,
    "read_linkedin_alerts": agent_api.read_linkedin_alerts,
    "ingest_batch": agent_api.ingest_batch,
    "list_watchlist": agent_api.list_watchlist,
    "add_watchlist_company": agent_api.add_watchlist_company,
    "check_watchlist": agent_api.check_watchlist,
    "analyze_job": agent_api.analyze_job,
    "generate_documents": agent_api.generate_documents,
    "list_applications": agent_api.list_applications,
}

assert {t["name"] for t in TOOLS} == set(_REGISTRY), "TOOLS and registry out of sync"


def dispatch(name: str, tool_input: dict) -> dict:
    """Run the tool `name` with the given input dict; always returns a dict."""
    fn = _REGISTRY.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool '{name}'"}
    try:
        return fn(**(tool_input or {}))
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments for {name}: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def tool_result_block(tool_use_id: str, result: dict) -> dict:
    """Wrap a dispatch() result as an Anthropic tool_result content block."""
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": json.dumps(result)}
