@echo off
REM Run the committed unit test suite (stdlib unittest, no extra packages)
cd /d "%~dp0"
set PYCMD=
where py >nul 2>nul
if %errorlevel%==0 set PYCMD=py -3
if not defined PYCMD (
  where python >nul 2>nul
  if %errorlevel%==0 set PYCMD=python
)
if not defined PYCMD (
  echo ERROR: No Python found on PATH.
  pause
  exit /b 1
)
%PYCMD% -m unittest discover -s tests
echo.
echo Done (exit code %errorlevel%).
pause