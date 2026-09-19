"""Analysis engine — calls the Claude API to extract requirements from a job
posting, screen it against the candidate's filters and red-flag patterns, pick
the best resume angle, and produce a fit score.

Returns a plain dict. The prompt asks for strict JSON so downstream code stays
simple.
"""

import json
import os
import anthropic

import config
from ats_keywords import keyword_gap


ANALYSIS_SYSTEM = """You are a job-fit analyst for a specific candidate. You \
read a job posting and return a strict-JSON assessment of how well it fits the \
candidate, using the candidate profile and filters provided. Be honest and \
calibrated — a high score should mean a genuinely strong match, not optimism. \
Return ONLY the JSON object, no prose, no markdown fences."""


def _build_prompt(job_text: str, profile: dict) -> str:
    angles = "\n".join(
        f"  - {key}: {a['label']} — {a['summary'].strip()}"
        for key, a in profile["angles"].items()
    )
    red_flags = "\n".join(f"  - {rf}" for rf in profile.get("red_flags", []))
    f = profile["filters"]

    return f"""CANDIDATE SNAPSHOT
Name: {profile['name']} | Location: {profile['location']} | {profile['languages']}
6+ years in wealth management / portfolio management; managed $1B+ AUM across
1,000+ portfolios. Bloomberg (BMC), Orion, Salesforce, Python, R, advanced Excel,
Power BI, applied AI tools. M.S. Finance (UM Herbert), MBA in progress (FIU).

HARD FILTERS
  - Salary floor: ${f['salary_floor']:,} (a max below this is a DEALBREAKER)
  - Remote strongly preferred: {f['remote_preference']}
  - Treat SQL as a *hard* requirement as a negative flag: {f['avoid_hard_sql']}
  - On-site is an automatic skip: {f['avoid_onsite']}

RESUME ANGLES (pick the single best-fitting key)
{angles}

KNOWN RED-FLAG PATTERNS (check the posting against these)
{red_flags}

JOB POSTING
\"\"\"
{job_text}
\"\"\"

Return a JSON object with exactly these keys:
{{
  "company": string or null,
  "title": string or null,
  "location": string or null,
  "remote": "remote" | "hybrid" | "onsite" | "unknown",
  "salary_low": number or null,
  "salary_high": number or null,
  "hard_requirements": [string, ...],
  "nice_to_haves": [string, ...],
  "sql_required": {{"required": boolean, "hard": boolean, "note": string}},
  "red_flags_detected": [string, ...],
  "dealbreakers": [string, ...],
  "best_angle": one of {list(profile['angles'].keys())},
  "match_reasons": [string, ...],
  "gap_reasons": [string, ...],
  "fit_score": integer 0-100,
  "recommendation_note": string
}}

Scoring guidance: weigh how well the candidate's actual experience matches the
hard requirements. Penalize dealbreakers heavily (salary below floor, hard SQL,
onsite when remote strongly preferred). A role squarely in wealth management /
FP&A / client success with salary at or above floor should score high; a role
demanding skills the candidate lacks (e.g. hard SQL, engineering) should score
lower with the gap stated plainly."""


def analyze(job_text: str, profile: dict, client: anthropic.Anthropic = None) -> dict:
    """Run the analysis. Adds a derived 'recommendation' field from the score."""
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    resp = client.messages.create(
        model=config.MODEL,
        max_tokens=config.MAX_TOKENS,
        system=ANALYSIS_SYSTEM,
        messages=[{"role": "user", "content": _build_prompt(job_text, profile)}],
    )
    raw = "".join(block.text for block in resp.content if block.type == "text")
    result = parse_json(raw)
    result["recommendation"] = _recommend(result)
    # Stage-2 ATS check: deterministic keyword overlap (no extra API call).
    result["keyword_gap"] = keyword_gap(job_text, profile, result)
    return result


def parse_json(raw: str) -> dict:
    """Parse model output into a dict, tolerating stray fences or preamble."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1:
            return json.loads(cleaned[start : end + 1])
        raise


def _recommend(result: dict) -> str:
    """Derive apply / apply-with-caution / skip from score + dealbreakers."""
    if result.get("dealbreakers"):
        return "skip"
    score = result.get("fit_score", 0) or 0
    if score >= config.APPLY_THRESHOLD:
        return "apply"
    if score >= config.CAUTION_THRESHOLD:
        return "apply-with-caution"
    return "skip"
