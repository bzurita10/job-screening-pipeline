"""ATS keyword-gap checker.

Stage-2 of ATS filtering is keyword matching: the system scores your resume by
how well its terms overlap the job description, then applies knockouts. This
module makes that visible BEFORE you apply.

It is fully deterministic (no API calls): it matches the job posting and the
candidate profile against a curated finance / analyst / BI / AI taxonomy, so the
matches are meaningful terms rather than noise. It reports:

  - matched   : taxonomy terms present in BOTH the posting and the resume
  - missing   : terms the posting wants that the resume does not surface
  - coverage  : matched / (matched + missing), 0-100
  - priority  : missing terms that also appear in the analysis 'hard_requirements'
  - other_jd_terms : frequent out-of-taxonomy words, in case the JD uses jargon
                     the taxonomy doesn't know yet

Typical use (wired into analyze.py):

    from ats_keywords import keyword_gap
    result["keyword_gap"] = keyword_gap(job_text, profile, result)

Standalone:

    python ats_keywords.py jobs/greenhouse_fpa.txt          # uses profile.yaml
"""

import re
from collections import Counter

# -----------------------------------------------------------------------------
# TAXONOMY  —  canonical term -> surface forms to match (case-insensitive, whole
# word/phrase). Add aliases freely; keep canonical labels resume-ready. This is
# the finance / FP&A / analyst / BI / data / AI vocabulary that shows up in the
# roles this candidate targets.
# -----------------------------------------------------------------------------
TAXONOMY = {
    # Core FP&A / financial analysis
    "financial modeling": ["financial model", "financial modeling", "financial models"],
    "forecasting": ["forecast", "forecasting"],
    "budgeting": ["budget", "budgeting", "budgets"],
    "variance analysis": ["variance analysis", "variance drivers", "variances"],
    "driver-based modeling": ["driver-based", "driver based"],
    "scenario analysis": ["scenario analysis", "scenario modeling", "what-if"],
    "financial reporting": ["financial reporting", "management reporting"],
    "month-end close": ["month-end", "month end close", "close process"],
    "P&L": ["p&l", "profit and loss", "income statement"],
    "OpEx": ["opex", "operating expense", "operating expenses"],
    "CapEx": ["capex", "capital expenditure"],
    "revenue analysis": ["revenue analysis", "topline", "revenue forecasting"],
    "ARR/MRR": ["arr", "mrr", "recurring revenue"],
    "KPI reporting": ["kpi", "kpis", "key performance indicator"],
    "headcount planning": ["headcount", "workforce planning"],
    "DCF/valuation": ["dcf", "valuation", "discounted cash flow"],
    "GAAP": ["gaap"],
    "SEC reporting": ["sec reporting", "10-k", "10-q", "10k", "10q"],
    "statutory accounting": ["statutory accounting", "stat accounting"],
    "cost analysis": ["cost analysis", "cost accounting", "cogs", "cgs"],

    # Investments / wealth
    "portfolio management": ["portfolio management", "portfolio manager", "portfolios"],
    "AUM": ["aum", "assets under management"],
    "rebalancing": ["rebalanc"],
    "risk analysis": ["risk analysis", "risk management", "risk assessment"],
    "investment research": ["investment research", "equity research", "securities analysis"],
    "high-net-worth": ["high-net-worth", "high net worth", "hnw"],

    # Tools & platforms
    "Excel": ["excel", "spreadsheet", "sheets", "google sheets", "advanced excel"],
    "SQL": ["sql", "structured query language"],
    "Python": ["python"],
    "R": [r"\bR\b programming", "r programming"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "VBA": ["vba", "macros"],
    "Bloomberg": ["bloomberg"],
    "Salesforce": ["salesforce"],
    "Orion": ["orion"],
    "Snowflake": ["snowflake"],
    "ETL": ["etl", "data pipeline"],
    "Anaplan": ["anaplan"],
    "Hyperion/Essbase": ["hyperion", "essbase"],
    "NetSuite": ["netsuite"],
    "SAP": ["sap"],
    "Workday Adaptive": ["adaptive planning", "workday adaptive"],

    # Data / analytics / BI
    "data analysis": ["data analysis", "data analytics", "analyze data"],
    "data visualization": ["data visualization", "dashboards", "dashboard"],
    "reporting automation": ["reporting automation", "automate reporting", "automation"],
    "statistical analysis": ["statistical analysis", "statistics", "regression"],
    "trend analysis": ["trend analysis", "trends"],
    "large datasets": ["large datasets", "large data sets"],

    # AI
    "generative AI": ["generative ai", "genai", "gen ai", "llm", "large language model"],
    "AI tools": ["ai tools", "chatgpt", "claude", "copilot", "openai"],
    "prompt engineering": ["prompt engineering", "prompting"],

    # Soft / process
    "cross-functional collaboration": ["cross-functional", "cross functional", "business partnering", "partner with"],
    "process improvement": ["process improvement", "process standardization", "streamline"],
    "stakeholder communication": ["stakeholder", "non-finance", "present to leadership", "communicate insights"],
    "bilingual (EN/ES)": ["bilingual", "spanish"],

    # Domain / environment
    "SaaS": ["saas", "software as a service"],
    "fintech": ["fintech", "financial technology"],
    "regulatory compliance": ["regulatory", "compliance"],
}

_STOP = set(
    "the a an and or of to in for with on at by from as is are be will you your our we "
    "this that these those role team work working experience years year ability strong "
    "excellent knowledge skills skill including etc across within their them they it its "
    "who what when where which while into out over per about more most other any all can "
    "have has had do does help support build built using use used able plus preferred "
    "required requirements responsibilities qualifications company join looking new day "
    "days month monthly quarterly annual detailed clear related field degree bachelor "
    "master direct directly closely broader routine".split()
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def extract_terms(text: str) -> set:
    """Return the set of canonical taxonomy terms present in `text`."""
    t = _norm(text)
    found = set()
    for canonical, forms in TAXONOMY.items():
        for form in forms:
            # forms that already look like regex (contain \b) are used as-is;
            # plain forms get word-boundary wrapping so 'r' doesn't match 'reporting'
            pat = form if "\\b" in form else r"(?<![a-z0-9])" + re.escape(form) + r"(?![a-z0-9])"
            if re.search(pat, t):
                found.add(canonical)
                break
    return found


def candidate_terms(profile: dict) -> set:
    """Everything the candidate can credibly claim, drawn from the profile."""
    parts = []
    for grp in profile.get("skills", {}).values():
        parts.extend(grp)
    for job in profile.get("experience", []):
        for b in job.get("bullets", []):
            parts.append(b["text"] if isinstance(b, dict) else b)
    for ang in profile.get("angles", {}).values():
        parts.append(ang.get("summary", ""))
    for ed in profile.get("education", []):
        parts.append(ed.get("degree", ""))
    return extract_terms(" \n ".join(parts))


def other_jd_terms(job_text: str, top_n: int = 8) -> list:
    """Frequent out-of-taxonomy single words — jargon the taxonomy may miss."""
    known = " ".join(f for forms in TAXONOMY.values() for f in forms)
    words = re.findall(r"[a-zA-Z][a-zA-Z\-/&]{3,}", _norm(job_text))
    counts = Counter(
        w for w in words
        if w not in _STOP and w not in known and not w.isdigit()
    )
    return [w for w, _ in counts.most_common(top_n)]


def keyword_gap(job_text: str, profile: dict, analysis: dict = None) -> dict:
    """Compare posting keywords to the candidate's. Returns a report dict.

    If `analysis` (the analyze.py result) is passed, missing terms that appear in
    its hard_requirements are elevated to `priority` — fix those first.
    """
    jd = extract_terms(job_text)
    cand = candidate_terms(profile)

    matched = sorted(jd & cand)
    missing = sorted(jd - cand)

    priority = []
    if analysis:
        hard_blob = _norm(" ".join(analysis.get("hard_requirements", [])))
        for term in missing:
            if any(_norm(f).strip("\\b") in hard_blob for f in TAXONOMY[term]):
                priority.append(term)

    total = len(matched) + len(missing)
    coverage = round(100 * len(matched) / total) if total else 100

    return {
        "coverage": coverage,
        "matched": matched,
        "missing": missing,
        "priority_missing": priority,
        "other_jd_terms": other_jd_terms(job_text),
    }


def format_report(gap: dict) -> str:
    """Human-readable summary for the CLI / tracker notes."""
    lines = [f"ATS keyword coverage: {gap['coverage']}%  "
             f"({len(gap['matched'])} matched / {len(gap['missing'])} missing)"]
    if gap["priority_missing"]:
        lines.append("  PRIORITY (in hard requirements, add if truthful): "
                     + ", ".join(gap["priority_missing"]))
    if gap["missing"]:
        lines.append("  Missing: " + ", ".join(gap["missing"]))
    if gap["matched"]:
        lines.append("  Matched: " + ", ".join(gap["matched"]))
    if gap["other_jd_terms"]:
        lines.append("  Other frequent JD words to review: "
                     + ", ".join(gap["other_jd_terms"]))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys, yaml, config

    if len(sys.argv) < 2:
        print("usage: python ats_keywords.py <job_posting.txt> [profile.yaml]")
        raise SystemExit(1)

    job_path = sys.argv[1]
    profile_path = sys.argv[2] if len(sys.argv) > 2 else config.PROFILE_PATH
    try:
        with open(profile_path) as fh:
            profile = yaml.safe_load(fh)
    except FileNotFoundError:
        # fall back to the shipped example so the module is testable out of the box
        with open(config.ROOT / "profile.example.yaml") as fh:
            profile = yaml.safe_load(fh)

    with open(job_path) as fh:
        job_text = fh.read()

    print(format_report(keyword_gap(job_text, profile)))
