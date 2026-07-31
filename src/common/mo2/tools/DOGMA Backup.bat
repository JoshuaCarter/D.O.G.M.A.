@echo off
setlocal
REM D.O.G.M.A. Backup - MCM diff, user.ltx, modlist, mods archive
REM Saves under <MO2>\DOGMA\backups\<timestamp>\
REM Logs: <MO2>\DOGMA\logs\
call "%~dp0run_py.bat" backup %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo Something went wrong with D.O.G.M.A. Backup.
  echo Details: %~dp0..\..\..\..\DOGMA\logs\dogma_install.log
) else (
  echo Finished.
)
pause
exit /b %ERR%
