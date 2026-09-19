"""Append each analyzed role to a CSV tracker, mirroring the columns from your
master summary's application tracker."""

import csv
from datetime import date

import config

FIELDS = [
    "date", "status", "company", "title", "location", "remote",
    "salary_low", "salary_high", "fit_score", "recommendation",
    "best_angle", "resume_file", "cover_letter_file",
]


def record(analysis: dict, resume_file=None, cover_file=None, status="screened"):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not config.TRACKER_PATH.exists()
    row = {
        "date": date.today().isoformat(),
        "status": status,
        "company": analysis.get("company"),
        "title": analysis.get("title"),
        "location": analysis.get("location"),
        "remote": analysis.get("remote"),
        "salary_low": analysis.get("salary_low"),
        "salary_high": analysis.get("salary_high"),
        "fit_score": analysis.get("fit_score"),
        "recommendation": analysis.get("recommendation"),
        "best_angle": analysis.get("best_angle"),
        "resume_file": resume_file.name if resume_file else "",
        "cover_letter_file": cover_file.name if cover_file else "",
    }
    with open(config.TRACKER_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    return config.TRACKER_PATH
