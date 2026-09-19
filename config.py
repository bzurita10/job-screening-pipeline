"""Central configuration for the job-screening / resume pipeline."""

from pathlib import Path

# --- Paths -------------------------------------------------------------------
ROOT = Path(__file__).parent
PROFILE_PATH = ROOT / "profile.yaml"
OUTPUT_DIR = ROOT / "output"
DATA_DIR = ROOT / "data"
TRACKER_PATH = DATA_DIR / "applications.csv"

# --- Model -------------------------------------------------------------------
# Swap this for whichever Anthropic model you want to run against.
# Sonnet is the right cost/quality balance for extraction + drafting.
MODEL = "claude-sonnet-5"
MAX_TOKENS = 2000

# Web-search tool (used by intake.from_search to find a JD from a title/URL).
# Bump the version string if Anthropic ships a newer web_search tool.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

# --- Formatting --------------------------------------------------------------
ACCENT_HEX = "1F4E79"   # Pantone 294 blue, matches your existing resume set
BODY_FONT = "Calibri"
NAME_FONT = "Calibri"

# --- Scoring ----------------------------------------------------------------
# Recommendation thresholds on the 0-100 fit score.
APPLY_THRESHOLD = 70          # >= this  -> "apply"
CAUTION_THRESHOLD = 50        # >= this  -> "apply-with-caution", below -> "skip"
