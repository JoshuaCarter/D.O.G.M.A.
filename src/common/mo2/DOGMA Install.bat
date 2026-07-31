@echo off
setlocal
REM DOGMA Install - register DOGMA bats in MO2 Executables:
REM   Setup / Backup / Restore / Optimize
REM Close MO2 first (ini is overwritten on MO2 exit).
REM Re-run safe: removes old DOGMA exe rows, then adds current tools.
REM Logs: <MO2>\DOGMA\logs\
call "%~dp0tools\run_py.bat" executables %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo *** DOGMA Install failed ***
  echo Log: %~dp0..\..\..\DOGMA\logs\dogma_install.log
) else (
  echo Done. Log: %~dp0..\..\..\DOGMA\logs\dogma_install.log
)
pause
exit /b %ERR%
