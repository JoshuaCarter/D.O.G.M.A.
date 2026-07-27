@echo off
setlocal
REM DOGMA (SFX Prefetch)
call "%~dp0run_job.bat" sfx %*
exit /b %ERRORLEVEL%
