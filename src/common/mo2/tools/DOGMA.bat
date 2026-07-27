@echo off
setlocal
REM DOGMA MO2 pre-launch runner (internal; under mo2\tools\).
REM Shipped to mods\DOGMA\mo2\tools\ from src\common\mo2\tools\.
REM
REM === Modify Executables (recommended play button) ===
REM   Title    = DOGMA
REM   Binary   = <MO2>\mods\DOGMA\mo2\tools\DOGMA.bat
REM   Start in = <MO2 instance root>   (e.g. C:\GAMMA)
REM   Arguments= --then-launch AnomalyDX11AVX.exe
REM              (use whatever exe you normally run: DX11 / AVX / DX9 / …)
REM
REM Runs optional mo2\prelaunch.steps (if present), then starts the game.
REM SFX prefetch: run DOGMA SFX Prefetch.bat when needed — not on every launch.
REM
REM Requires Python 3. No pause.
REM Do not use %%ERRORLEVEL%% inside ( ) — expands at parse time.

where py >nul 2>nul
if errorlevel 1 goto :try_python
py -3 "%~dp0prelaunch.py" %*
exit /b %ERRORLEVEL%

:try_python
where python >nul 2>nul
if errorlevel 1 goto :try_python3
python "%~dp0prelaunch.py" %*
exit /b %ERRORLEVEL%

:try_python3
where python3 >nul 2>nul
if errorlevel 1 goto :no_python
python3 "%~dp0prelaunch.py" %*
exit /b %ERRORLEVEL%

:no_python
echo Python 3 not found. Install from https://www.python.org/downloads/
echo Or: winget install Python.Python.3.12
exit /b 1
