@echo off
setlocal
REM D.O.G.M.A. Optimize - GC, SFX prefetch, full DOGMA Backup, ALAO
REM Backup lands in <MO2>\DOGMA\backups\<timestamp>\
REM Log: <MO2>\DOGMA\logs\
call "%~dp0run_py.bat" optimize %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo Something went wrong with D.O.G.M.A. Optimizer.
  echo Details: %~dp0..\..\..\..\DOGMA\logs\dogma_install.log
) else (
  echo Finished.
)
pause
exit /b %ERR%
