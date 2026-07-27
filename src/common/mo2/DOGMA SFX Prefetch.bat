@echo off
setlocal
REM DOGMA SFX Prefetch — user-facing entry (implementation in tools\)
REM Log: mods\DOGMA\mo2\logs\dogma_sfx_prefetch.log
call "%~dp0tools\run_job.bat" sfx %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo *** DOGMA SFX Prefetch failed ***
  echo Log: %~dp0logs\dogma_sfx_prefetch.log
) else (
  echo Done. Log: %~dp0logs\dogma_sfx_prefetch.log
)
pause
exit /b %ERR%
