@echo off
setlocal
REM DOGMA (Disable Mods)
call "%~dp0_run_job.bat" disable %*
exit /b %ERRORLEVEL%
