@echo off
setlocal
REM DOGMA (Install+) — required + suggested
call "%~dp0DOGMA (Setup Tools).bat"
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" dependencies --tier all --mode reinstall %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" disable --tier all %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" defaults %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" sfx %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" validate --tier all %*
exit /b %ERRORLEVEL%
