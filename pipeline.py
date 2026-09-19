#!/usr/bin/env python3
"""Job-screening & resume-tailoring pipeline.

Usage:
    python pipeline.py path/to/job.txt          # analyze a saved description
    python pipeline.py --paste                  # paste the description
    python pipeline.py --url https://...         # fetch a public posting
    python pipeline.py job.txt --dry-run         # score only, no documents
    python pipeline.py job.txt --no-cover        # resume only (no API cover letter)
    python pipeline.py job.txt --force           # generate even if recommendation = skip

Set your key first:  export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import sys
import yaml
import anthropic

import config
import intake
import analyze
import generate
import tracker


def load_profile():
    with open(config.PROFILE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def print_report(a):
    line = "=" * 62
    print(f"\n{line}")
    print(f"  {a.get('title') or '?'}  —  {a.get('company') or '?'}")
    print(line)
    sal = ""
    if a.get("salary_low") or a.get("salary_high"):
        lo = f"${a['salary_low']:,}" if a.get("salary_low") else "?"
        hi = f"${a['salary_high']:,}" if a.get("salary_high") else "?"
        sal = f"{lo} – {hi}"
    print(f"  Location   : {a.get('location') or '?'}  ({a.get('remote')})")
    print(f"  Salary     : {sal or 'not listed'}")
    print(f"  Fit score  : {a.get('fit_score')}/100")
    print(f"  Best angle : {a.get('best_angle')}")
    print(f"  Verdict    : {a.get('recommendation','').upper()}")
    kg = a.get("keyword_gap")
    if kg:
        print(f"  Keyword fit: {kg['coverage']}% ATS keyword coverage")
        if kg.get("priority_missing"):
            print("    Add first (named in hard requirements, only if truthful):")
            print("      " + ", ".join(kg["priority_missing"]))
        elif kg.get("missing"):
            print("    Missing terms: " + ", ".join(kg["missing"][:8]))
    if a.get("dealbreakers"):
        print("  DEALBREAKERS:")
        for d in a["dealbreakers"]:
            print(f"    - {d}")
    if a.get("red_flags_detected"):
        print("  Red flags:")
        for r in a["red_flags_detected"]:
            print(f"    - {r}")
    if a.get("match_reasons"):
        print("  Why it fits:")
        for m in a["match_reasons"][:4]:
            print(f"    + {m}")
    if a.get("gap_reasons"):
        print("  Gaps:")
        for g in a["gap_reasons"][:4]:
            print(f"    - {g}")
    if a.get("recommendation_note"):
        print(f"\n  Note: {a['recommendation_note']}")
    print(line)


def keywords_from(analysis):
    """Flatten requirement text into lowercase tokens for bullet matching."""
    text = " ".join(
        analysis.get("hard_requirements", []) + analysis.get("nice_to_haves", [])
    ).lower()
    return text.split()


def main():
    p = argparse.ArgumentParser(description="Job-screening & resume pipeline")
    p.add_argument("jobfile", nargs="?", help="path to a job-description text file")
    p.add_argument("--paste", action="store_true", help="paste the description via stdin")
    p.add_argument("--url", help="fetch a public job-posting URL")
    p.add_argument("--search", help="find a posting by title/company via web search, e.g. \"Financial Analyst, DVC FP&A, Disney\"")
    p.add_argument("--dry-run", action="store_true", help="analyze only; no documents")
    p.add_argument("--no-cover", action="store_true", help="skip the cover letter")
    p.add_argument("--force", action="store_true", help="generate even if verdict = skip")
    args = p.parse_args()

    profile = load_profile()
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        job_text = intake.load(args)
    except Exception as e:
        print(f"Intake failed: {e}", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    a = analyze.analyze(job_text, profile, client=client)
    print_report(a)

    if args.dry_run:
        tracker.record(a)
        print(f"\nLogged to {config.TRACKER_PATH} (dry run — no documents).")
        return

    if a["recommendation"] == "skip" and not args.force:
        tracker.record(a)
        print("\nVerdict is SKIP — no documents generated. Use --force to override.")
        return

    docs = generate.build_both(
        profile, a["best_angle"], job=a, keywords=keywords_from(a)
    )
    print(f"\nResume (polished) -> {docs['polished']}")
    print(f"Resume (ATS-safe) -> {docs['ats']}")

    # Stage-1 ATS check: lint each generated resume for parser-killers.
    import ats_lint
    for label, path in (("polished", docs["polished"]), ("ATS-safe", docs["ats"])):
        lint_res = ats_lint.lint(str(path))
        print(f"ATS-lint ({label}) -> {lint_res['verdict']}")
        if lint_res["verdict"] == "FAIL":
            for c in lint_res["checks"]:
                if c["status"] == "FAIL":
                    print(f"    ! {c['name']}: {c['detail']}")

    cover = None
    if not args.no_cover:
        cover = generate.build_cover_letter(profile, a, client=client)
        print(f"Cover   -> {cover}")

    tracker.record(a, resume_file=docs["polished"], cover_file=cover)
    print(f"Tracker -> {config.TRACKER_PATH}")


if __name__ == "__main__":
    main()
