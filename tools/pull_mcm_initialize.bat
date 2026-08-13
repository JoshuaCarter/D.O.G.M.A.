@echo off
setlocal
REM Removed. Edit config\mcm_config.yml by hand; build snapshots axr.

cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0pull_mcm_initialize.py" %*
  set ERR=%ERRORLEVEL%
  goto :done
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%~dp0pull_mcm_initialize.py" %*
  set ERR=%ERRORLEVEL%
  goto :done
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 "%~dp0pull_mcm_initialize.py" %*
  set ERR=%ERRORLEVEL%
  goto :done
)

echo Python 3 not found. Install from https://www.python.org/downloads/
set ERR=1

:done
echo.
pause
exit /b %ERR%
