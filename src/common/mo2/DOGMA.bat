@echo off
setlocal
REM DOGMA MO2 pre-launch runner (DOGMA.bat).
REM Shipped to mods\DOGMA\mo2\ from src\common\mo2\.
REM
REM === Modify Executables (recommended play button) ===
REM   Title    = DOGMA
REM   Binary   = <MO2>\mods\DOGMA\mo2\DOGMA.bat
REM   Start in = <MO2 instance root>   (e.g. C:\GAMMA)
REM   Arguments= --then-launch AnomalyDX11AVX.exe
REM              (use whatever exe you normally run: DX11 / AVX / DX9 / …)
REM
REM Runs every non-comment line in prelaunch.ini, then starts the game.
REM Add more pre-launch tools by editing prelaunch.ini (same folder).
REM
REM Requires Python 3. No pause.

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0prelaunch.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%~dp0prelaunch.py" %*
  exit /b %ERRORLEVEL%
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
  python3 "%~dp0prelaunch.py" %*
  exit /b %ERRORLEVEL%
)

echo Python 3 not found. Install from https://www.python.org/downloads/
echo Or: winget install Python.Python.3.12
exit /b 1
