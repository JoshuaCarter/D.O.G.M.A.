@echo off
setlocal
REM D.O.G.M.A. Setup - user-facing entry (pipeline in dogma_job.py install)
REM Skip GUI: set DOGMA_NO_WIZARD=1  or pass --no-wizard
REM Logs: <MO2>\DOGMA\logs\
call "%~dp0run_py.bat" install --log-reset %*
set "ERR=%ERRORLEVEL%"
if "%ERR%"=="2" exit /b 2
if not "%ERR%"=="0" (
  echo.
  echo *** DOGMA Setup failed ***
  echo Log: %~dp0..\..\..\..\DOGMA\logs\dogma_install.log
  echo Report (if any): %~dp0..\..\..\..\DOGMA\logs\dogma_report.log
  pause
)
exit /b %ERR%
