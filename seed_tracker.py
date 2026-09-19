#!/usr/bin/env python3
"""Optionally preload the tracker with applications you've already made, so
data/applications.csv starts as your live pipeline instead of empty.

The lists below are ILLUSTRATIVE EXAMPLES — replace them with your own roles.
Run once against a fresh tracker (it appends).
"""

import re
import config
import tracker


def salary(text):
    """Parse strings like '~$90-120K', '$125K est.', '$70-90K' -> (low, high).

    'K' often appears only on the last figure of a range ('90-120K'), so treat
    every bare number as thousands whenever a K is present anywhere.
    """
    if not text:
        return None, None
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)]
    if not nums:
        return None, None
    if "K" in text.upper():
        nums = [n * 1000 if n < 1000 else n for n in nums]
    return min(nums), max(nums)


# (company, title, salary_text, angle, cover_letter_file) - EXAMPLES; replace.
CONFIRMED = [
    ("Example Corp", "FP&A Analyst", "$90-110K", "FA", ""),
    ("Sample Wealth Advisors", "Client Service Associate", "$75-90K", "CSA", ""),
]

# (company, title, salary_text, angle) - EXAMPLES; replace.
PENDING = [
    ("Acme Financial", "Senior Financial Analyst", "~$95-120K", "FA"),
    ("Globex Capital", "AI Operations Analyst", "~$100-125K", "FinTech_AI"),
    ("Initech Advisors", "Bilingual Financial Analyst", "~$85-110K", "Bilingual_FA"),
]


def seed():
    for company, title, sal, angle, cover in CONFIRMED:
        lo, hi = salary(sal)
        tracker.record(
            {"company": company, "title": title, "location": None, "remote": None,
             "salary_low": lo, "salary_high": hi, "fit_score": None,
             "recommendation": None, "best_angle": angle},
            cover_file=type("F", (), {"name": cover})() if cover else None,
            status="applied",
        )
    for company, title, sal, angle in PENDING:
        lo, hi = salary(sal)
        tracker.record(
            {"company": company, "title": title, "location": None, "remote": None,
             "salary_low": lo, "salary_high": hi, "fit_score": None,
             "recommendation": None, "best_angle": angle},
            status="pending",
        )
    print(f"Seeded {len(CONFIRMED)} applied + {len(PENDING)} pending "
          f"-> {config.TRACKER_PATH}")


if __name__ == "__main__":
    seed()
