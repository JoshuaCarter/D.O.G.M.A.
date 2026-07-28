@echo off
setlocal
REM DOGMA Sound Prefetch builder (feature-local; also via DOGMA SFX Prefetch.bat).
REM Shipped to mods\DOGMA\mo2\ from src\misc\sound_prefetch\mo2\.
REM Requires Python 3. No pause.

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0build_sound_prefetch.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%~dp0build_sound_prefetch.py" %*
  exit /b %ERRORLEVEL%
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 "%~dp0build_sound_prefetch.py" %*
  exit /b %ERRORLEVEL%
)

echo Python 3 not found. Install from https://www.python.org/downloads/
echo Or: winget install Python.Python.3.12
exit /b 1
