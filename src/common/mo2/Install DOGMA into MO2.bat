@echo off
setlocal
REM Install DOGMA into MO2 — register DOGMA bats in MO2 Executables.
REM Close MO2 first (ini is overwritten on MO2 exit).
REM Re-run safe: removes old DOGMA exe rows, then adds:
REM   D.O.G.M.A. Setup / Apply Defaults / Optimize
REM Logs: mods\DOGMA\mo2\logs\
call "%~dp0tools\run_py.bat" executables %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo *** Install DOGMA into MO2 failed ***
  echo Log: %~dp0logs\dogma_install.log
) else (
  echo Done. Log: %~dp0logs\dogma_install.log
)
pause
exit /b %ERR%
