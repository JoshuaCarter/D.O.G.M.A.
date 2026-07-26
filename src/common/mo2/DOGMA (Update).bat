@echo off
setlocal
REM DOGMA (Update) — ensure deps; defaults only for new/changed sections
call "%~dp0DOGMA (Setup Tools).bat"
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" dependencies --tier all --mode ensure %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" disable --tier all %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" defaults --fingerprint %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" sfx %*
if errorlevel 1 exit /b %ERRORLEVEL%
call "%~dp0_run_job.bat" validate --tier all %*
exit /b %ERRORLEVEL%
