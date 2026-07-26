@echo off
setlocal
REM DOGMA (SFX Prefetch)
call "%~dp0_run_job.bat" sfx %*
exit /b %ERRORLEVEL%
