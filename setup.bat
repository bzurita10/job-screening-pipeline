@echo off
REM ===========================================================================
REM setup.bat - one-command setup for the job-screening pipeline on Windows.
REM Double-click it, or run it from a terminal. It locates its own folder, so
REM it works wherever you unzipped this, no path editing required.
REM
REM It will: check Python, install dependencies, check your API key, flag any
REM unfilled contact placeholders in profile.yaml, then verify the watchlist.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
echo.
echo ============================================================
echo   Job-screening pipeline - setup
echo   Folder: %CD%
echo ============================================================
echo.

REM --- 1. Python on PATH? ---------------------------------------------------
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   ERROR: 'python' was not found on your PATH.
    echo   Install Python from python.org and tick "Add python.exe to PATH",
    echo   then re-open your terminal and run setup.bat again.
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo   Found %%v

REM --- 2. Install dependencies ----------------------------------------------
echo.
echo [2/5] Installing dependencies from requirements.txt...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo   ERROR: dependency install failed. Scroll up for the reason.
    echo.
    pause
    exit /b 1
)
echo   Dependencies OK.

REM --- 3. API key -----------------------------------------------------------
echo.
echo [3/5] Checking ANTHROPIC_API_KEY...
if "%ANTHROPIC_API_KEY%"=="" (
    echo   NOT SET for this terminal.
    echo   The scanner and resume generator work without it; only --analyze
    echo   and cover-letter drafting need it. To set it permanently:
    echo       setx ANTHROPIC_API_KEY "sk-ant-..."
    echo   then CLOSE and re-open your terminal ^(setx only affects new ones^).
) else (
    echo   Set OK ^(value hidden^).
)

REM --- 4. Contact placeholders still in profile.yaml? ----------------------
echo.
echo [4/5] Checking profile.yaml contacts...
findstr /c:"<<your" profile.yaml >nul 2>&1
if errorlevel 1 (
    echo   Contacts look filled in.
) else (
    echo   REMINDER: profile.yaml still has placeholder contacts:
    findstr /n /c:"<<your" profile.yaml
    echo   Open profile.yaml and replace phone / email / linkedin before you
    echo   generate resumes ^(the ATS lint will flag missing contacts until then^).
)

REM --- 5. Verify the watchlist boards resolve ------------------------------
echo.
echo [5/5] Verifying watchlist boards resolve ^(this hits the network^)...
echo.
python scan.py --check

echo.
echo ============================================================
echo   Setup done. Next steps:
echo     python scan.py                    ^(see what's new^)
echo     python scan.py --analyze --top 5  ^(score top 5 - uses API^)
echo     python pipeline.py jobs\greenhouse_fpa.txt
echo   See SMOKETEST.md for the full 5-minute verification.
echo ============================================================
echo.
pause
endlocal
