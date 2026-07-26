@echo off
setlocal
REM DOGMA (Apply Defaults)
call "%~dp0_run_job.bat" defaults %*
exit /b %ERRORLEVEL%
