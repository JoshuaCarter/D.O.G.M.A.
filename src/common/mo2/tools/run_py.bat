@echo off
setlocal
REM Shared: find Python and run dogma_job.py with forwarded args.
REM %* should start with the subcommand (install|defaults|executables|…).
REM Do not use %%ERRORLEVEL%% inside ( ) — it expands at parse time and
REM always reports the prior where.exe success (0).
REM Name must NOT start with "_" — tools/build.sh skips _* files.

where py >nul 2>nul
if errorlevel 1 goto :try_python
py -3 "%~dp0dogma_job.py" %*
exit /b %ERRORLEVEL%

:try_python
where python >nul 2>nul
if errorlevel 1 goto :try_python3
python "%~dp0dogma_job.py" %*
exit /b %ERRORLEVEL%

:try_python3
where python3 >nul 2>nul
if errorlevel 1 goto :no_python
python3 "%~dp0dogma_job.py" %*
exit /b %ERRORLEVEL%

:no_python
echo Python 3 not found. Run DOGMA Setup once, or install from https://www.python.org/downloads/
exit /b 1
