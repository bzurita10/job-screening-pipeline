@echo off
REM run_health.bat - weekly watchlist health check on Windows.
REM Schedule this weekly (e.g. Monday 07:45, before the daily scan) so a broken
REM board slug surfaces on its own instead of silently returning no jobs.
REM Self-locating: runs from its own folder, no path editing needed.

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

if not exist logs mkdir logs
echo ===== HEALTH %date% %time% ===== >> logs\health.log
python scan.py --health health >> logs\health.log 2>&1
echo. >> logs\health.log
