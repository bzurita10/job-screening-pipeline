#!/usr/bin/env python3
"""paste_batch.py — paste MANY job descriptions at once; split, score, rank.

The fallback path for anything the pollers can't reach (LinkedIn postings,
Workday boards, a role someone emails you). Instead of one screenshot per job,
you paste a single blob containing several postings separated by a delimiter,
and the script splits it, scores each one, and prints a ranked shortlist.

Usage:
    python paste_batch.py                    # paste, then Ctrl-D (Ctrl-Z on Windows)
    python paste_batch.py --file blob.txt
    python paste_batch.py --file blob.txt --generate

Separate postings with a line containing only:   ---
(or "===", or a line starting with "### ")

Tip for collecting quickly: on a job page use Ctrl-A / Ctrl-C — the surrounding
nav text is harmless, the analyzer ignores it. Paste, type ---, paste the next.
"""

import argparse
import re
import sys

import yaml

import config

SPLIT = re.compile(r"^\s*(?:-{3,}|={3,}|#{3}\s+.*)\s*$", re.M)


def split_blob(text: str):
    chunks = [c.strip() for c in SPLIT.split(text)]
    return [c for c in chunks if len(c) > 120]   # ignore stray fragments


def main():
    p = argparse.ArgumentParser(description="Score a batch of pasted job descriptions")
    p.add_argument("--file", help="read the blob from a file instead of stdin")
    p.add_argument("--generate", action="store_true", help="build documents for non-skip verdicts")
    p.add_argument("--save-dir", default="jobs/pasted", help="where to save each split posting")
    args = p.parse_args()

    if args.file:
        blob = open(args.file, encoding="utf-8").read()
    else:
        print("Paste postings separated by a line of ---, then Ctrl-D:\n", file=sys.stderr)
        blob = sys.stdin.read()

    postings = split_blob(blob)
    if not postings:
        print("No postings found.", file=sys.stderr)
        sys.exit(1)
    print(f"\nSplit into {len(postings)} posting(s).\n")

    out_dir = config.ROOT / args.save_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    import anthropic
    import analyze as analyzer
    import generate
    import tracker
    from pipeline import keywords_from, print_report

    with open(config.PROFILE_PATH) as fh:
        profile = yaml.safe_load(fh)
    client = anthropic.Anthropic()
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for i, text in enumerate(postings, 1):
        print(f"[{i}/{len(postings)}] analyzing...", flush=True)
        try:
            a = analyzer.analyze(text, profile, client=client)
        except Exception as e:  # noqa: BLE001
            print(f"    failed: {e}")
            continue
        slug = re.sub(r"[^a-z0-9]+", "_",
                      f"{a.get('company') or 'unknown'}_{a.get('title') or i}".lower()).strip("_")[:70]
        (out_dir / f"{slug}.txt").write_text(text)
        results.append((a, text))

        if args.generate and a["recommendation"] != "skip":
            d = generate.build_both(profile, a["best_angle"], job=a, keywords=keywords_from(a))
            r = d["polished"]
            import ats_lint
            verdict = ats_lint.lint(str(d["ats"]))["verdict"]
            print(f"    resume -> {r}  (+ ATS twin, lint {verdict})")
            tracker.record(a, resume_file=r)
        else:
            tracker.record(a)

    results.sort(key=lambda t: t[0].get("fit_score") or 0, reverse=True)
    line = "=" * 72
    print(f"\n{line}\n  RANKED SHORTLIST\n{line}")
    for a, _ in results:
        kg = a.get("keyword_gap", {})
        print(f"  {a.get('fit_score'):>3}/100  {a.get('recommendation','').upper():18} "
              f"{a.get('title')} — {a.get('company')}  (kw {kg.get('coverage','?')}%)")
    print(line)
    print(f"\nSaved {len(results)} posting(s) to {out_dir}/ and logged to {config.TRACKER_PATH}")


if __name__ == "__main__":
    main()
