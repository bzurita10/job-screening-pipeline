#!/usr/bin/env bash
# run_scan.sh — one command for a scheduled daily scan.
# Edit PIPELINE_DIR to the absolute path of this folder on your machine.
#
# What it does: activates the virtualenv if present, runs the scan, writes a
# dated markdown digest, and appends everything to logs/scan.log so a failed
# run leaves a trace. Add --analyze to score matches (uses your API key).

set -euo pipefail

PIPELINE_DIR="${PIPELINE_DIR:-$HOME/job-screening-pipeline}"
cd "$PIPELINE_DIR"

# Activate a venv if you made one (recommended). Harmless if it doesn't exist.
[ -f ".venv/bin/activate" ] && source .venv/bin/activate

# Your Claude API key must be visible to cron/launchd — set it here or in the
# crontab. Leave commented if you only run plain scans (no --analyze).
# export ANTHROPIC_API_KEY="sk-ant-..."

mkdir -p logs
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  python scan.py --digest digests
  echo
} >> logs/scan.log 2>&1
