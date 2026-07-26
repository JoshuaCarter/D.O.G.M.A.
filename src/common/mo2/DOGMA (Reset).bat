@echo off
setlocal
REM DOGMA (Reset) — FRESH_INSTALL + MCM values, then Install
call "%~dp0DOGMA (Setup Tools).bat"
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" reset-base %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0DOGMA (Install).bat" %*
exit /b %ERRORLEVEL%
