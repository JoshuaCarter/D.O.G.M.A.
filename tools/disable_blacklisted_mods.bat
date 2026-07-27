@echo off
setlocal EnableDelayedExpansion
REM Cross-platform DOGMA MO2 helper (Python). This .bat is the Windows launcher.
REM Close Mod Organizer 2 / the game before running (modlist edits).
REM
REM Requires Python 3 (https://www.python.org/downloads/ or `winget install Python.Python.3.12`)
REM
REM Defaults look under the repo config\ folder:
REM   config\features.yml + mods.yml  (features, packs, disables, mcm)
REM   config\user.ltx
REM   (legacy *.ini kept as reference only)
REM
REM Usage:
REM   disable_blacklisted_mods.bat
REM   disable_blacklisted_mods.bat --dry-run
REM   disable_blacklisted_mods.bat --disable-only
REM   disable_blacklisted_mods.bat --no-pause
REM   ( --no-pause for optional mo2/prelaunch.steps — do not block launch )

cd /d "%~dp0"

set PAUSE_AT_END=1
echo %*| findstr /i /c:"--no-pause" >nul && set PAUSE_AT_END=0

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0disable_blacklisted_mods.py" %*
  set ERR=!ERRORLEVEL!
  goto :done
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%~dp0disable_blacklisted_mods.py" %*
  set ERR=!ERRORLEVEL!
  goto :done
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 "%~dp0disable_blacklisted_mods.py" %*
  set ERR=!ERRORLEVEL!
  goto :done
)

echo Python 3 not found. Install from https://www.python.org/downloads/
echo Or: winget install Python.Python.3.12
set ERR=1

:done
if "!PAUSE_AT_END!"=="1" (
  echo.
  pause
)
exit /b %ERR%
