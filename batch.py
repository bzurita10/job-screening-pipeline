#!/usr/bin/env python3
"""Batch screening — point it at a folder of job-description .txt files and get
one ranked shortlist, plus tailored documents for the strong matches.

Usage:
    python batch.py                       # screen ./jobs, generate for "apply" verdicts
    python batch.py path/to/folder        # screen a different folder
    python batch.py --dry-run             # rank only; generate no documents
    python batch.py --top 5               # generate only for the top 5 ranked
    python batch.py --no-cover            # resumes only, skip cover letters

Each file becomes one row. Results are ranked by fit score (skips sink to the
bottom), written to output/shortlist_<date>.csv, and logged to the tracker.
Set your key first:  export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import anthropic
import yaml

import config
import intake
import analyze
import generate
import tracker
from pipeline import keywords_from

SHORTLIST_FIELDS = [
    "rank", "fit_score", "recommendation", "company", "title",
    "salary_low", "salary_high", "remote", "best_angle",
    "dealbreakers", "source_file",
]

# a stable ordering so "apply" floats above "caution" above "skip" at equal score
REC_ORDER = {"apply": 0, "apply-with-caution": 1, "skip": 2}


def load_profile():
    with open(config.PROFILE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def screen_folder(folder: Path, profile: dict, client) -> tuple[list, list]:
    """Analyze every .txt in the folder. Returns (results, errors)."""
    files = sorted(folder.glob("*.txt"))
    if not files:
        raise FileNotFoundError(f"No .txt job files found in {folder}")

    results, errors = [], []
    for i, path in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {path.name} ...", end=" ", flush=True)
        try:
            text = intake.from_file(str(path))
            a = analyze.analyze(text, profile, client=client)
            a["source_file"] = path.name
            results.append(a)
            print(f"{a['fit_score']}/100 -> {a['recommendation']}")
        except Exception as e:  # one bad posting shouldn't sink the batch
            errors.append((path.name, str(e)))
            print(f"ERROR: {e}")
    return results, errors


def rank(results: list) -> list:
    """Sort by recommendation tier, then fit score (desc)."""
    return sorted(
        results,
        key=lambda a: (REC_ORDER.get(a.get("recommendation"), 9),
                       -(a.get("fit_score") or 0)),
    )


def write_shortlist(ranked: list) -> Path:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = config.OUTPUT_DIR / f"shortlist_{date.today().isoformat()}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHORTLIST_FIELDS)
        w.writeheader()
        for i, a in enumerate(ranked, 1):
            w.writerow({
                "rank": i,
                "fit_score": a.get("fit_score"),
                "recommendation": a.get("recommendation"),
                "company": a.get("company"),
                "title": a.get("title"),
                "salary_low": a.get("salary_low"),
                "salary_high": a.get("salary_high"),
                "remote": a.get("remote"),
                "best_angle": a.get("best_angle"),
                "dealbreakers": "; ".join(a.get("dealbreakers", [])),
                "source_file": a.get("source_file"),
            })
    return out


def print_table(ranked: list):
    line = "-" * 92
    print("\n" + line)
    print(f"{'#':>2}  {'SCORE':>5}  {'VERDICT':<19}  {'COMPANY':<20}  {'TITLE':<26}  ANGLE")
    print(line)
    for i, a in enumerate(ranked, 1):
        print(f"{i:>2}  {str(a.get('fit_score') or '?'):>5}  "
              f"{(a.get('recommendation') or '?'):<19}  "
              f"{(a.get('company') or '?')[:20]:<20}  "
              f"{(a.get('title') or '?')[:26]:<26}  {a.get('best_angle') or '?'}")
    print(line)


def main():
    p = argparse.ArgumentParser(description="Batch job screening -> ranked shortlist")
    p.add_argument("folder", nargs="?", default="jobs", help="folder of .txt postings")
    p.add_argument("--dry-run", action="store_true", help="rank only; no documents")
    p.add_argument("--top", type=int, help="generate documents only for the top N")
    p.add_argument("--no-cover", action="store_true", help="resumes only, no cover letters")
    args = p.parse_args()

    profile = load_profile()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    folder = Path(args.folder)
    print(f"Screening {folder}/ ...")
    results, errors = screen_folder(folder, profile, client)
    if not results:
        print("Nothing analyzed successfully.", file=sys.stderr)
        sys.exit(1)

    ranked = rank(results)
    print_table(ranked)
    shortlist = write_shortlist(ranked)
    print(f"Shortlist -> {shortlist}")

    # decide which roles get documents
    if args.dry_run:
        for a in ranked:
            tracker.record(a)
        print("Dry run — logged to tracker, no documents generated.")
        return

    to_generate = ranked[: args.top] if args.top else [
        a for a in ranked if a.get("recommendation") == "apply"
    ]
    gen_names = {a["source_file"] for a in to_generate}

    print(f"\nGenerating documents for {len(to_generate)} role(s):")
    for a in ranked:
        resume = coverf = None
        if a["source_file"] in gen_names and a.get("recommendation") != "skip":
            resume = generate.build_resume(
                profile, a["best_angle"], job=a, keywords=keywords_from(a))
            if not args.no_cover:
                coverf = generate.build_cover_letter(profile, a, client=client)
            print(f"  + {a.get('company')} — {a.get('title')}  "
                  f"({resume.name}{', ' + coverf.name if coverf else ''})")
        tracker.record(a, resume_file=resume, cover_file=coverf)

    print(f"\nDone. Documents in {config.OUTPUT_DIR}/, tracker updated.")
    if errors:
        print(f"\n{len(errors)} file(s) failed:")
        for name, err in errors:
            print(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
