@echo off
REM run_scan.bat - one command for a scheduled daily scan on Windows.
REM Self-locating: it runs from its own folder, so no path editing is needed
REM as long as this file stays inside the pipeline folder.

cd /d "%~dp0"

REM Activate a venv if present
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

REM Set your key here if you use --analyze
REM set "ANTHROPIC_API_KEY=sk-ant-..."

if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\scan.log
python scan.py --digest digests >> logs\scan.log 2>&1
echo. >> logs\scan.log
