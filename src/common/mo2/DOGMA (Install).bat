@echo off
setlocal
REM DOGMA (Install) — required deps + disable + defaults + SFX + validate
call "%~dp0DOGMA (Setup Tools).bat"
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" dependencies --tier required --mode reinstall %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" disable --tier required %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" defaults %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" sfx %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" validate --tier required %*
exit /b %ERRORLEVEL%
