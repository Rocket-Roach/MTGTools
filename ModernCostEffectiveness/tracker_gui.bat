@echo off
REM MTG Modern Deck Tracker - GUI launcher (double-click to run)
cd /d "%~dp0"
echo ============================================================
echo  MTG Modern Deck Tracker
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

%PYCMD% --version
echo Starting the app...
echo.
%PYCMD% src\tracker_gui.py
echo.
echo App closed (exit code %errorlevel%).
pause