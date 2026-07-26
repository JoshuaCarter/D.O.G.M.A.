@echo off
setlocal
REM Pull non-default MCM options from axr_options.ltx into config\initialize.ini
REM
REM Requires Python 3.
REM
REM Usage:
REM   pull_mcm_initialize.bat
REM   pull_mcm_initialize.bat --dry-run
REM   pull_mcm_initialize.bat --include-dogma
REM   pull_mcm_initialize.bat --all-saved
REM   pull_mcm_initialize.bat --mo2-root "D:\Games\GAMMA"

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
