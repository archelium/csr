@echo off
REM ===================================================================
REM  CSR - Citizen Service Record : launcher (runs the local app)
REM  Opens the dashboard in your browser. On first run it asks you to
REM  pick your StarCitizen\LIVE folder. Keep this window open while using
REM  CSR; the Refresh button in the page re-scans your latest logs.
REM ===================================================================
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py sc_stats.py --serve
) else (
    python sc_stats.py --serve
)

echo.
echo CSR stopped. Press any key to close.
pause >nul
