@echo off
setlocal
REM DOGMA (Setup) — wizard for packs + feature depends, then install pipeline
REM Skip GUI: set DOGMA_NO_WIZARD=1  (uses default installer_options)
REM Logs: mods\DOGMA\mo2\logs\dogma_install.log + dogma_report.log
call "%~dp0DOGMA (Setup Tools).bat" --log-reset %*
if errorlevel 1 goto :fail

set "SEL="
if /I "%DOGMA_NO_WIZARD%"=="1" goto :jobs
call "%~dp0run_job.bat" wizard %*
if errorlevel 1 goto :fail
set "SEL=--use-selection"

:jobs
call "%~dp0run_job.bat" dependencies --tier all --mode reinstall %SEL% %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" disable --tier all %SEL% %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" defaults %SEL% %*
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
echo *** DOGMA (Setup) failed ***
echo Log: %~dp0logs\dogma_install.log
echo Report (if any): %~dp0logs\dogma_report.log
pause
exit /b 1
