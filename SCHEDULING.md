# Running the scan automatically (daily)

`scan.py` remembers what it has already shown you (`data/seen_jobs.json`), so a
scheduled run only ever surfaces genuinely new postings. Each run writes a dated
digest to `digests/YYYY-MM-DD.md` and appends to `logs/scan.log`.

Pick the section for your operating system. In all cases, first make sure a
plain manual run works:

```bash
python scan.py --check      # confirm your watchlist slugs resolve
python scan.py --digest     # one real run; look at digests/ and the console
```

The wrappers (`run_scan.sh`, `run_scan.bat`) are what the scheduler calls. Open
the one for your OS and set the pipeline path (and your API key, only if you
want scheduled runs to `--analyze`).

---

## macOS

macOS ships with `cron`, which is the simplest option.

1. Make the wrapper runnable and set the path inside it:
   ```bash
   chmod +x run_scan.sh
   # edit PIPELINE_DIR at the top of run_scan.sh to this folder's full path
   ```
2. Open your crontab:
   ```bash
   crontab -e
   ```
3. Add one line — this runs every weekday at 8:00 AM:
   ```
   0 8 * * 1-5 /full/path/to/job-screening-pipeline/run_scan.sh
   ```
4. Give `cron` permission if macOS prompts: System Settings > Privacy &
   Security > Full Disk Access > add `/usr/sbin/cron`.

To have it `--analyze` automatically, uncomment the `ANTHROPIC_API_KEY` line
inside `run_scan.sh` and change the command there to `python scan.py --analyze
--digest digests`.

**launchd alternative** (survives reboots more reliably than cron on Macs): ask
and I'll generate a ready `.plist` — but cron is fine to start.

---

## Linux

Same as macOS:

```bash
chmod +x run_scan.sh
crontab -e
# weekdays at 8:00 AM:
0 8 * * 1-5 /full/path/to/job-screening-pipeline/run_scan.sh
```

If the machine isn't always on, use `anacron` or a systemd timer instead so a
missed run catches up. Ask if you want the systemd unit files.

---

## Windows (Task Scheduler)

1. Open **Task Scheduler** > **Create Basic Task**. (`run_scan.bat` is
   self-locating — no path editing needed, it runs from its own folder.)
2. Name it "Job scan", trigger **Daily** (or Weekly, Mon–Fri), start time 8:00 AM.
3. Action: **Start a program** > Program/script: browse to `run_scan.bat`.
4. Finish. To limit it to weekdays, open the task > **Triggers** > Edit >
   **Weekly** > check Mon–Fri.

Or from an Administrator PowerShell, in one line:

```powershell
schtasks /create /tn "Job scan" /tr "C:\full\path\to\run_scan.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 08:00
```

---

## What you'll see each morning

- `digests/2026-09-15.md` — the day's new matches, ranked, with links. Skim it,
  open the ones worth pursuing.
- `logs/scan.log` — a running record; check here if a morning produces nothing
  (a bad slug or a network hiccup shows up as an error line).

## Cadence tips

- Daily on weekdays is plenty; most boards refresh a few times a week.
- Keep the scan plain (no `--analyze`) for the scheduled job — it's fast, free,
  and the digest is enough to triage. Run `python scan.py --analyze --top 5`
  by hand on the mornings something good shows up.
- If a company's slug starts erroring in the log, re-open its careers page and
  update the slug in `watchlist.yaml`; boards get renamed occasionally.

# Weekly: watchlist health check

The daily scan stays quiet when a board breaks — a bad slug just returns no
jobs, which looks the same as a slow week. A weekly health check catches that.
`python scan.py --health` fetches every board once and writes a dated report to
`health/`, distinguishing three states:

- **OK** — resolved, has postings.
- **EMPTY** — resolved fine, 0 postings right now (**not** a problem).
- **BROKEN** — the fetch errored (bad slug/ATS, 404/403). **This is what to fix.**

Only BROKEN boards are flagged loudly; empty boards are listed quietly so a
genuinely dead slug can't hide among companies that simply aren't hiring.

**Windows:** schedule `run_health.bat` weekly, ideally just before Monday's scan:
```
schtasks /create /tn "Watchlist health" /tr "C:\full\path\to\run_health.bat" /sc weekly /d MON /st 07:45
```

**macOS/Linux (cron):**
```
45 7 * * 1 cd /path/to/pipeline && python3 scan.py --health >> logs/health.log 2>&1
```

Reports and `logs/health.log` are git-ignored. When a report shows BROKEN, open
that company's careers page, click a job, read the board domain in the address
bar, and correct `ats`/`slug` in `watchlist.yaml`.

