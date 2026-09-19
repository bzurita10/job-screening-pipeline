#!/usr/bin/env python3
"""Research batch — give it a LIST OF TITLES, get a ranked shortlist + resumes.

This removes the manual "open every posting and screenshot the description"
step. You provide only the role names (copy them off a results page, or from
one screenshot); the pipeline researches each full description via web search,
scores it, ranks them, and generates documents for the strong matches.

Titles file: one role per line. Blank lines and lines starting with '#' are
skipped. Freeform is fine; more context = better search hits, e.g.:

    Financial Analyst, DVC FP&A, Disney, Celebration FL
    Sr Financial Analyst, WDI Project Finance, Disney, Lake Buena Vista
    Pricing Analyst, Disney Experiences, Lake Buena Vista FL

Usage:
    python research.py roles.txt                # research -> rank -> generate
    python research.py --paste                  # paste the list of titles
    python research.py roles.txt --dry-run      # rank only; no documents
    python research.py roles.txt --top 5        # generate only for the top 5
    python research.py roles.txt --no-cover     # resumes only

Set your key first:  export ANTHROPIC_API_KEY=sk-ant-...
Note: each title costs ~1 search + 1 scoring call (plus a cover-letter call for
"apply" verdicts), so a long list uses a fair number of API calls.
"""

import argparse
import sys
from pathlib import Path

import anthropic

import config
import intake
import analyze
import generate
import tracker
from pipeline import keywords_from
from batch import rank, write_shortlist, print_table, load_profile


def read_titles(args) -> list[str]:
    if getattr(args, "paste", False):
        print("Paste your list of titles (one per line), then Ctrl-D:\n")
        raw = sys.stdin.read()
    else:
        raw = Path(args.titlefile).read_text(encoding="utf-8")
    titles = []
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            titles.append(line)
    if not titles:
        raise ValueError("No titles found (blank file or all comments).")
    return titles


def research(titles: list[str], profile: dict, client) -> tuple[list, list]:
    """For each title: find the JD via web search, then score it."""
    results, errors = [], []
    for i, query in enumerate(titles, 1):
        print(f"  [{i}/{len(titles)}] {query[:60]} ...", end=" ", flush=True)
        try:
            jd = intake.from_search(query, client)
            a = analyze.analyze(jd, profile, client=client)
            a["source_file"] = query           # keep the query as provenance
            results.append(a)
            print(f"{a['fit_score']}/100 -> {a['recommendation']}")
        except Exception as e:
            errors.append((query, str(e)))
            print(f"MISS: {e}")
    return results, errors


def main():
    p = argparse.ArgumentParser(description="Research a list of titles -> ranked shortlist + resumes")
    p.add_argument("titlefile", nargs="?", help="text file of role titles, one per line")
    p.add_argument("--paste", action="store_true", help="paste the list of titles via stdin")
    p.add_argument("--dry-run", action="store_true", help="rank only; no documents")
    p.add_argument("--top", type=int, help="generate documents only for the top N")
    p.add_argument("--no-cover", action="store_true", help="resumes only, no cover letters")
    args = p.parse_args()

    if not args.titlefile and not args.paste:
        p.error("give a titles file or use --paste")

    profile = load_profile()
    client = anthropic.Anthropic()

    titles = read_titles(args)
    print(f"Researching {len(titles)} role(s) via web search ...")
    results, errors = research(titles, profile, client)
    if not results:
        print("\nNothing could be researched successfully.", file=sys.stderr)
        if errors:
            for q, e in errors:
                print(f"  - {q}: {e}", file=sys.stderr)
        sys.exit(1)

    ranked = rank(results)
    print_table(ranked)
    shortlist = write_shortlist(ranked)
    print(f"Shortlist -> {shortlist}")

    if args.dry_run:
        for a in ranked:
            tracker.record(a)
        print("Dry run — logged to tracker, no documents generated.")
    else:
        to_gen = ranked[: args.top] if args.top else [
            a for a in ranked if a.get("recommendation") == "apply"
        ]
        gen = {a["source_file"] for a in to_gen}
        print(f"\nGenerating documents for {len(to_gen)} role(s):")
        for a in ranked:
            resume = coverf = None
            if a["source_file"] in gen and a.get("recommendation") != "skip":
                resume = generate.build_resume(
                    profile, a["best_angle"], job=a, keywords=keywords_from(a))
                if not args.no_cover:
                    coverf = generate.build_cover_letter(profile, a, client=client)
                print(f"  + {a.get('company')} — {a.get('title')}")
            tracker.record(a, resume_file=resume, cover_file=coverf)
        print(f"\nDone. Documents in {config.OUTPUT_DIR}/, tracker updated.")

    if errors:
        print(f"\n{len(errors)} title(s) couldn't be researched (search miss or "
              f"JS-only site — screenshot those):")
        for q, e in errors:
            print(f"  - {q}")


if __name__ == "__main__":
    main()
