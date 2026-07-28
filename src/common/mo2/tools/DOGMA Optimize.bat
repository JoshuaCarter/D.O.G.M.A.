@echo off
setlocal
REM D.O.G.M.A. Optimize — GC, SFX prefetch, mods backup, ALAO
REM MO2 Arguments = backup dir (default C:\GAMMA; ini stores C:\\GAMMA); %%1
REM Log: mods\DOGMA\mo2\logs\
call "%~dp0run_py.bat" optimize %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo Something went wrong with D.O.G.M.A. Optimizer.
  echo Details: %~dp0..\logs\dogma_install.log
) else (
  echo Finished.
)
pause
exit /b %ERR%
