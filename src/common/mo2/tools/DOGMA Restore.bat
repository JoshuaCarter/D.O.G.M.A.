@echo off
setlocal
REM D.O.G.M.A. Restore — pick a backup, restore MCM / user.ltx / modlist / mods
REM Backups: <MO2>\DOGMA\backups\
REM Close MO2 first when restoring modlist or mods.
REM Logs: <MO2>\DOGMA\logs\
call "%~dp0run_py.bat" restore %*
set "ERR=%ERRORLEVEL%"
echo.
if "%ERR%"=="2" exit /b 2
if not "%ERR%"=="0" (
  echo Something went wrong with D.O.G.M.A. Restore.
  echo Details: %~dp0..\..\..\..\DOGMA\logs\dogma_install.log
) else (
  echo Finished.
)
pause
exit /b %ERR%
