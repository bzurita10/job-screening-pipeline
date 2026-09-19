"""ATS-lint — scan a .docx resume for the formatting that breaks ATS parsers.

Runs entirely on the standard library (zipfile + ElementTree). It inspects the
document's XML for the failure modes confirmed across Workday, Greenhouse,
iCIMS, Taleo, and Lever, and prints a pass / warn / fail report.

Checks:
  1. Tables            -> FAIL  (parsers read across rows; titles/dates scramble)
  2. Multi-column body -> FAIL  (columns are read left-to-right and interleaved)
  3. Images / graphics -> FAIL  (text inside graphics is invisible to parsers)
  4. Text boxes        -> FAIL  (content often dropped)
  5. Header/footer text-> WARN  (many parsers skip these; keep contact in body)
  6. Section headings  -> WARN  (needs standard: Experience, Education, Skills)
  7. Parseable dates   -> WARN  (needs Month YYYY or MM/YYYY in the body)
  8. Contact in body   -> FAIL  (email + phone must be in the body text)
  9. Fonts             -> WARN  (stick to Calibri/Arial/Times/Georgia/Helvetica)

Use:
    python ats_lint.py output/Resume_ATS_FinancialAnalyst.docx

Wired into the pipeline, generate.py can call `lint(path)` after building a
resume and refuse to ship one that FAILs.
"""

import re
import sys
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

STANDARD_HEADINGS = {
    "summary", "professional summary", "career summary",
    "experience", "work experience", "professional experience", "employment history",
    "education",
    "skills", "technical skills", "core competencies",
    "certifications", "licenses & certifications",
    "projects", "key projects",
}
SAFE_FONTS = {"calibri", "arial", "times new roman", "georgia", "helvetica", "cambria", "garamond"}

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _read(zf, name):
    try:
        return zf.read(name).decode("utf-8", "replace")
    except KeyError:
        return ""


def _paragraph_texts(xml):
    """Return a list of paragraph strings from a document.xml string."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for p in root.iter(f"{W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{W}t"))
        if text.strip():
            out.append(text.strip())
    return out


def lint(path: str) -> dict:
    """Return {'verdict': PASS|WARN|FAIL, 'checks': [ {name, status, detail} ]}."""
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        doc = _read(zf, "word/document.xml")
        styles = _read(zf, "word/styles.xml")

        # 1. Tables
        n_tbl = doc.count(f"{W}tbl") or doc.count("<w:tbl>")
        add("Tables", FAIL if n_tbl else PASS,
            f"{n_tbl} table(s) found — rebuild without tables" if n_tbl
            else "No tables")

        # 2. Multi-column body
        cols = re.findall(r'<w:cols[^>]*w:num="(\d+)"', doc)
        multi = [c for c in cols if int(c) >= 2]
        add("Single column", FAIL if multi else PASS,
            f"{max(map(int, multi))}-column section — use single column" if multi
            else "Single-column layout")

        # 3. Images / graphics
        media = [n for n in names if n.startswith("word/media/")]
        has_blip = "a:blip" in doc or f"{W}drawing" in doc or "<w:drawing>" in doc
        add("No images/graphics", FAIL if (media or has_blip) else PASS,
            f"{len(media)} embedded image(s)/graphic(s) — parsers can't read text in images"
            if (media or has_blip) else "No images or graphics")

        # 4. Text boxes
        has_txbx = ("txbxContent" in doc) or ("v:textbox" in doc) or ("wps:txbx" in doc)
        add("No text boxes", FAIL if has_txbx else PASS,
            "Text box detected — move content into the body" if has_txbx
            else "No text boxes")

        # 5. Header/footer text
        hf = [n for n in names if re.match(r"word/(header|footer)\d*\.xml$", n)]
        hf_text = " ".join(_read(zf, n) for n in hf)
        hf_has_text = bool(re.search(r"[A-Za-z]{3,}", re.sub(r"<[^>]+>", "", hf_text)))
        add("Contact not in header/footer", WARN if hf_has_text else PASS,
            "Text found in header/footer — many parsers skip it; keep contact in the body"
            if hf_has_text else "No header/footer text")

        paras = _paragraph_texts(doc)
        body = "\n".join(paras)

        # 6. Section headings
        lowered = {p.lower().rstrip(":") for p in paras}
        found_headings = lowered & STANDARD_HEADINGS
        need_exp = any(h in lowered for h in
                       ("experience", "work experience", "professional experience", "employment history"))
        need_edu = "education" in lowered
        if need_exp and need_edu:
            add("Standard headings", PASS,
                "Found: " + ", ".join(sorted(found_headings)))
        else:
            missing = [h for h, ok in (("Experience", need_exp), ("Education", need_edu)) if not ok]
            add("Standard headings", WARN,
                "Missing standard heading(s): " + ", ".join(missing))

        # 7. Parseable dates
        date_pat = re.compile(
            r"(0?[1-9]|1[0-2])[/-]\d{4}"                       # MM/YYYY
            r"|(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}",  # Month YYYY
            re.I)
        n_dates = len(date_pat.findall(body))
        add("Parseable dates", PASS if n_dates >= 2 else WARN,
            f"{n_dates} date(s) in Month YYYY / MM/YYYY form" if n_dates >= 2
            else "Fewer than 2 clean dates found — use 'Month YYYY' on every role")

        # 8. Contact info in body
        has_email = bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", body))
        has_phone = bool(re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", body))
        add("Contact in body", PASS if (has_email and has_phone) else FAIL,
            "Email and phone present in body" if (has_email and has_phone)
            else f"Missing in body: {'email ' if not has_email else ''}{'phone' if not has_phone else ''}".strip())

        # 9. Fonts — only fonts actually APPLIED in the body, plus the document
        #    default. Latent fonts defined in unused styles (e.g. the "Courier New"
        #    that ships in python-docx's default template) never reach a parser.
        applied = set(re.findall(r'w:ascii="([^"]+)"', doc))
        dd = re.search(r'<w:docDefaults>.*?w:ascii="([^"]+)".*?</w:docDefaults>', styles, re.S)
        if dd:
            applied.add(dd.group(1))
        fonts = set(f.lower() for f in applied)
        unsafe = sorted(f for f in fonts if f and f not in SAFE_FONTS)
        add("ATS-safe fonts", WARN if unsafe else PASS,
            "Non-standard font(s): " + ", ".join(unsafe) if unsafe
            else "Standard fonts only")

    statuses = [c["status"] for c in checks]
    verdict = FAIL if FAIL in statuses else (WARN if WARN in statuses else PASS)
    return {"verdict": verdict, "checks": checks}


def format_report(result: dict) -> str:
    icon = {PASS: "[PASS]", WARN: "[WARN]", FAIL: "[FAIL]"}
    lines = [f"ATS-LINT VERDICT: {result['verdict']}", "-" * 48]
    for c in result["checks"]:
        lines.append(f"{icon[c['status']]:8} {c['name']}: {c['detail']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python ats_lint.py <resume.docx>")
        raise SystemExit(1)
    res = lint(sys.argv[1])
    print(format_report(res))
    raise SystemExit(1 if res["verdict"] == FAIL else 0)
