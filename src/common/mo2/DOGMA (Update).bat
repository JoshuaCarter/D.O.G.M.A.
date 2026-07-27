@echo off
setlocal
REM DOGMA (Update) — ensure deps, disable, defaults, sfx, validate
REM Uses saved wizard selection when present; else default installer_options.
REM Logs: mods\DOGMA\mo2\logs\dogma_install.log + dogma_report.log
call "%~dp0DOGMA (Setup Tools).bat" --log-reset %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" dependencies --tier all --mode ensure --use-selection %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" disable --tier all --use-selection %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" defaults --use-selection %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" sfx %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" validate --tier all --use-selection %*
if errorlevel 1 goto :fail
echo.
echo Logs: %~dp0logs\dogma_install.log
echo       %~dp0logs\dogma_report.log
exit /b 0

:fail
echo.
echo *** DOGMA (Update) failed ***
echo Log: %~dp0logs\dogma_install.log
pause
exit /b 1
