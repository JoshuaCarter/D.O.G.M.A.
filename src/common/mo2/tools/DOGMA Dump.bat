@echo off
setlocal
REM D.O.G.M.A. Dump - logs, MCM diff, user.ltx, modlist, hardware → zip
REM Saves under <MO2>\DOGMA\dumps\dogma_dump_<YYYY-MM-DD_HH-MM-SS>.zip
REM Tool log: <MO2>\DOGMA\logs\
call "%~dp0run_py.bat" dump %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo Something went wrong with D.O.G.M.A. Dump.
  echo Details: %~dp0..\..\..\..\DOGMA\logs\dogma_install.log
) else (
  echo Finished. Zip is under DOGMA\dumps\
)
pause
exit /b %ERR%
