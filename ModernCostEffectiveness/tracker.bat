@echo off
REM MTG Modern Deck Tracker - CLI launcher (double-click for interactive mode)
REM Usage: tracker.bat [import FILE | clipboard | progress | missing | collection]
cd /d "%~dp0"
echo ============================================================
echo  MTG Modern Deck Tracker (command line)
echo  Folder: %CD%
echo ============================================================
echo.

set PYCMD=
where py >nul 2>nul
if %errorlevel%==0 set PYCMD=py -3
if not defined PYCMD (
  where python >nul 2>nul
  if %errorlevel%==0 set PYCMD=python
)
if not defined PYCMD (
  echo ERROR: No Python found on PATH.
  echo Install Python 3 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

%PYCMD% src\tracker.py %*
echo.
echo Done (exit code %errorlevel%).
pause