@echo off
setlocal
REM Cross-platform DOGMA MO2 helper (Python). This .bat is the Windows launcher.
REM Close Mod Organizer 2 / the game before running.
REM
REM Requires Python 3 (https://www.python.org/downloads/ or `winget install Python.Python.3.12`)
REM
REM Defaults look under the repo config\ folder:
REM   config\disable.ini
REM   config\initialize.ini
REM   config\user.ltx
REM   config\manifest.ini
REM
REM Usage:
REM   disable_blacklisted_mods.bat
REM   disable_blacklisted_mods.bat --dry-run
REM   disable_blacklisted_mods.bat --disable-only
REM   disable_blacklisted_mods.bat --initialize-only
REM   disable_blacklisted_mods.bat --keybinds-only
REM   disable_blacklisted_mods.bat --user-ltx-only
REM   disable_blacklisted_mods.bat --mo2-root "D:\Games\GAMMA"
REM   disable_blacklisted_mods.bat --all-profiles
REM   disable_blacklisted_mods.bat --profile "GAMMA Custom"

cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0disable_blacklisted_mods.py" %*
  set ERR=%ERRORLEVEL%
  goto :done
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%~dp0disable_blacklisted_mods.py" %*
  set ERR=%ERRORLEVEL%
  goto :done
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 "%~dp0disable_blacklisted_mods.py" %*
  set ERR=%ERRORLEVEL%
  goto :done
)

echo Python 3 not found. Install from https://www.python.org/downloads/
echo Or: winget install Python.Python.3.12
set ERR=1

:done
echo.
pause
exit /b %ERR%
