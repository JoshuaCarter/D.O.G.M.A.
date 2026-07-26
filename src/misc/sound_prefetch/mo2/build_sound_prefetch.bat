@echo off
setlocal
REM DOGMA Sound Prefetch - MO2 Executable entry point.
REM Shipped to mods\DOGMA\mo2\ from src\misc\sound_prefetch\mo2\.
REM
REM Modify Executables:
REM   Binary   = <MO2>\mods\DOGMA\mo2\build_sound_prefetch.bat
REM   Start in = <MO2 instance root>  (e.g. C:\GAMMA)
REM
REM Separate tool: no arguments
REM Before every launch: --then-launch AnomalyDX11AVX.exe
REM
REM Requires Python 3. No pause - must not block MO2 launch.

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
