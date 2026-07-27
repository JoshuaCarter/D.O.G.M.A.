@echo off
setlocal
REM DOGMA (Disable Mods)
call "%~dp0run_job.bat" disable %*
exit /b %ERRORLEVEL%
