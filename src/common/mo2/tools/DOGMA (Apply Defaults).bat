@echo off
setlocal
REM DOGMA (Apply Defaults)
call "%~dp0run_job.bat" defaults %*
exit /b %ERRORLEVEL%
