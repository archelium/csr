@echo off
REM Build CSR.exe (one-file Windows app) from source.
REM Requires Python 3.9+ on PATH. Produces dist\CSR.exe
REM CSR is self-contained: it keeps its data in a CSR-data folder next to the exe
REM (portable), falling back to %LOCALAPPDATA%\CSR only if that spot is read-only.
cd /d "%~dp0"

echo [1/2] Ensuring PyInstaller is installed...
python -m pip install --quiet --upgrade pyinstaller || goto :err

echo [2/2] Building CSR.exe ...
python -m PyInstaller csr.spec --noconfirm --clean || goto :err

echo.
echo Done.  ->  dist\CSR.exe
echo Next:  python make_release.py   (assembles the shareable zip)
pause >nul
goto :eof

:err
echo.
echo Build failed. Make sure Python is installed and on PATH.
pause >nul
