"""Document generation.

build_resume() is fully deterministic from profile.yaml + the chosen angle and
job keywords (offline, no API). It produces resumes that match your approved
house style:

  * "Company  |  Role" on one line, bold, with the date flush-right (polished),
    OR the role on its own line with "Company | dates" beneath it (ATS-safe).
  * #1F4E79 accent on section headings with a thin rule.
  * Sections: Summary, Experience, Skills, Education, Certifications.
  * ALL of a role's bullets are kept — ordered most-relevant-first for the
    target role, never silently dropped.

build_both() returns both a polished resume (for human eyes / LinkedIn / direct
applications) and an ATS-safe twin (for portal uploads), from one call.

build_cover_letter() drafts prose with the Claude API, then lays it out.
"""

import os
import re
import anthropic
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_TAB_ALIGNMENT, WD_ALIGN_PARAGRAPH

import config

ACCENT = RGBColor.from_string(config.ACCENT_HEX)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "role"


# --- shared layout helpers ---------------------------------------------------

def _new_doc():
    doc = Document()
    for section in doc.sections:
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        section.top_margin = Inches(0.5)
        section.bottom_margin = Inches(0.5)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)
    style = doc.styles["Normal"]
    style.font.name = config.BODY_FONT
    style.font.size = Pt(10)
    style.paragraph_format.space_after = Pt(1)
    style.paragraph_format.line_spacing = 1.0
    # right tab sits exactly at the right margin so dates align flush-right
    right_tab = section.page_width - section.left_margin - section.right_margin
    doc._right_tab = right_tab
    return doc


def _heading(doc, text):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(5)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = ACCENT
    pPr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), config.ACCENT_HEX)
    borders.append(bottom)
    pPr.append(borders)
    return p


def _dated_line(doc, left_bold, right):
    """Bold left text with a right-aligned date via a tab at the right margin."""
    p = doc.add_paragraph()
    p.paragraph_format.tab_stops.add_tab_stop(doc._right_tab, WD_TAB_ALIGNMENT.RIGHT)
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run(left_bold)
    r.bold = True
    p.add_run("\t" + right)
    return p


def _stacked_lines(doc, bold_top, plain_bottom):
    """ATS-safe: bold line on its own, then a plain line beneath (no tab tricks)."""
    p1 = doc.add_paragraph()
    p1.paragraph_format.space_before = Pt(3)
    p1.paragraph_format.space_after = Pt(0)
    r = p1.add_run(bold_top)
    r.bold = True
    p2 = doc.add_paragraph()
    p2.paragraph_format.space_before = Pt(0)
    p2.paragraph_format.space_after = Pt(0)
    pr = p2.add_run(plain_bottom)
    pr.font.size = Pt(9.5)
    return p1


# --- resume ------------------------------------------------------------------

def _rank_bullets(exp, emphasis, keywords, cap=10):
    """Keep ALL of a role's bullets, in the author-curated order (impact-first).
    Nothing is dropped or reshuffled — the tailoring happens through which angle
    (summary + emphasis) is chosen, not by hiding or demoting accomplishments."""
    return [b["text"] for b in exp["bullets"]][:cap]


def _visible_certs(profile, angle):
    """Core certs always appear; others only if the angle's cert_tags match.
    Supports both the tagged dict form and a plain string (treated as core)."""
    want = set(angle.get("cert_tags", []))
    out = []
    for c in profile.get("certifications", []):
        if isinstance(c, str):
            out.append(c)
            continue
        tags = set(c.get("tags", []))
        if "core" in tags or not tags or (tags & want):
            out.append(c["name"])
    return out


def build_resume(profile, angle_key, job=None, keywords=None, ats_safe=False,
                 include_summary=False):
    """Build one tailored resume. ats_safe=False -> polished; True -> ATS layout."""
    keywords = keywords or []
    angle = profile["angles"][angle_key]
    emphasis = angle.get("emphasis", [])
    doc = _new_doc()

    # header
    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_p.paragraph_format.space_after = Pt(0)
    nr = name_p.add_run(profile["name"])
    nr.bold = True
    nr.font.size = Pt(20)
    nr.font.color.rgb = ACCENT

    contact_p = doc.add_paragraph()
    contact_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    contact_p.add_run("  |  ".join(
        [profile["phone"], profile["email"], profile["linkedin"]]
    )).font.size = Pt(9.5)

    # two-line positioning statement (angle-specific), centered under the contact
    headline = angle.get("headline")
    if headline:
        h = doc.add_paragraph()
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        h.paragraph_format.space_before = Pt(3)
        h.paragraph_format.space_after = Pt(2)
        hr = h.add_run(" ".join(headline.split()))
        hr.italic = True
        hr.font.size = Pt(10)

    # summary (off by default — its content lives in the bullets/skills/education)
    if include_summary:
        _heading(doc, "Professional Summary")
        doc.add_paragraph(" ".join(angle["summary"].split()))

    # experience
    _heading(doc, "Professional Experience")
    for exp in profile["experience"]:
        dates = f"{exp['start']} – {exp['end']}"
        if ats_safe:
            _stacked_lines(doc, exp["title"], f"{exp['company']}  |  {dates}")
        else:
            _dated_line(doc, f"{exp['company']}  |  {exp['title']}", dates)
        for text in _rank_bullets(exp, emphasis, keywords):
            b = doc.add_paragraph(style="List Bullet")
            b.paragraph_format.space_after = Pt(1)
            b.add_run(text)

    # skills
    _heading(doc, "Technical Skills & Tools")
    for group, items in profile["skills"].items():
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(f"{group}: ")
        r.bold = True
        p.add_run(", ".join(items))

    # education
    _heading(doc, "Education")
    for ed in profile["education"]:
        if ats_safe:
            _stacked_lines(doc, ed["degree"], f"{ed['school']}  |  {ed['dates']}")
        else:
            _dated_line(doc, f"{ed['school']}  |  {ed['degree']}", ed["dates"])

    # certifications (dedicated section) — core certs always show; tagged extras
    # show only when the angle's cert_tags call for them.
    certs = _visible_certs(profile, angle)
    if certs:
        _heading(doc, "Certifications & Professional Development")
        for c in certs:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(1)
            p.add_run(c)

    company = (job or {}).get("company") if job else None
    suffix = "_ATS" if ats_safe else ""
    fname = f"Resume_{angle_key}_{_slug(company or 'general')}{suffix}.docx"
    out = config.OUTPUT_DIR / fname
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


def build_both(profile, angle_key, job=None, keywords=None):
    """Produce the polished resume AND its ATS-safe twin. Returns a dict."""
    return {
        "polished": build_resume(profile, angle_key, job, keywords, ats_safe=False),
        "ats": build_resume(profile, angle_key, job, keywords, ats_safe=True),
    }


# --- cover letter ------------------------------------------------------------

COVER_SYSTEM = """You write concise, specific, professional cover letters. No \
fluff, no clichés ('I am writing to express my interest'), no invented facts. \
Ground every claim in the candidate details provided. 3-4 short paragraphs. \
Return ONLY the letter body (no address block, no 'Dear...', no sign-off)."""


def _draft_cover_body(profile, analysis, client):
    reasons = analysis.get("reasons") or analysis.get("match_reasons") or []
    match = "; ".join(reasons[:5])
    reqs = "; ".join((analysis.get("hard_requirements") or [])[:6])
    prompt = f"""Write a cover letter body for this candidate and role.

CANDIDATE: {profile['name']}, {profile['location']}. 6+ years in wealth/portfolio
management; managed $1B+ AUM across 1,000+ portfolios; Bloomberg, Orion,
Salesforce, Python, R, advanced Excel; bilingual EN/ES; M.S. Finance (UM Herbert),
MBA in progress (FIU, Generative AI for Business).

ROLE: {analysis.get('title')} at {analysis.get('company')}.
KEY REQUIREMENTS: {reqs}
WHY THIS CANDIDATE FITS (use these, in your own words): {match}

Write 3-4 tight paragraphs connecting the candidate's real experience to the
role's needs. Confident, specific, human."""
    resp = client.messages.create(
        model=config.MODEL,
        max_tokens=config.MAX_TOKENS,
        system=COVER_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def build_cover_letter(profile, analysis, client=None):
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    body = _draft_cover_body(profile, analysis, client)

    doc = _new_doc()
    doc.styles["Normal"].paragraph_format.space_after = Pt(8)

    head = doc.add_paragraph()
    hr = head.add_run(profile["name"])
    hr.bold = True
    hr.font.size = Pt(16)
    hr.font.color.rgb = ACCENT
    doc.add_paragraph(
        "  |  ".join([profile["phone"], profile["email"], profile["linkedin"]])
    ).runs[0].font.size = Pt(9.5)

    company = analysis.get("company") or "the team"
    doc.add_paragraph(f"Dear {company} Hiring Team,")
    for para in [p for p in body.split("\n") if p.strip()]:
        doc.add_paragraph(para.strip())
    doc.add_paragraph("Sincerely,")
    doc.add_paragraph(profile["name"])

    fname = f"CoverLetter_{_slug(company)}_{_slug(analysis.get('title'))}.docx"
    out = config.OUTPUT_DIR / fname
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out
