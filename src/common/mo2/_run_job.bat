@echo off
setlocal
REM Shared: find Python and run dogma_job.py with forwarded args.
REM %* should start with the subcommand (setup|dependencies|…).

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0dogma_job.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%~dp0dogma_job.py" %*
  exit /b %ERRORLEVEL%
)
where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 "%~dp0dogma_job.py" %*
  exit /b %ERRORLEVEL%
)
echo Python 3 not found. Run "DOGMA (Setup Tools).bat" or install from https://www.python.org/downloads/
exit /b 1
