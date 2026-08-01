@echo off
setlocal
REM DOGMA SFX Prefetch
REM Log: <MO2>\DOGMA\logs\dogma_sfx_prefetch.log
call "%~dp0run_py.bat" sfx %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo *** DOGMA SFX Prefetch failed ***
  echo Log: %~dp0..\..\..\..\DOGMA\logs\dogma_sfx_prefetch.log
) else (
  echo Done. Log: %~dp0..\..\..\..\DOGMA\logs\dogma_sfx_prefetch.log
)
pause
exit /b %ERR%
