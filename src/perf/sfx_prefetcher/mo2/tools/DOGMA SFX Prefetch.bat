@echo off
setlocal
REM DOGMA SFX Prefetch - scan enabled mods into overwrite/gamedata/configs/dogma_sfx_prefetch.ltx
REM Lives next to dogma_sfx_prefetch.py (perf/sfx_prefetcher mo2/tools).

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0dogma_sfx_prefetch.py" %*
  goto :done
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0dogma_sfx_prefetch.py" %*
  goto :done
)
where python3 >nul 2>nul
if not errorlevel 1 (
  python3 "%~dp0dogma_sfx_prefetch.py" %*
  goto :done
)
echo Python 3 not found. Run DOGMA Setup once, or install from https://www.python.org/downloads/
exit /b 1

:done
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo *** DOGMA SFX Prefetch failed ***
) else (
  echo Done.
)
pause
exit /b %ERR%
