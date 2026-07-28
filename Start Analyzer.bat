@echo off
REM ===================================================================
REM  SC Logs Live Analyzer - double-click launcher
REM  Auto-detects your Game.log, opens the dashboard in your browser.
REM ===================================================================
cd /d "%~dp0"

REM Prefer the Python launcher, fall back to python on PATH.
where py >nul 2>nul
if %errorlevel%==0 (
    py sc_analyzer.py
) else (
    python sc_analyzer.py
)

echo.
echo Analyzer stopped. Press any key to close.
pause >nul
