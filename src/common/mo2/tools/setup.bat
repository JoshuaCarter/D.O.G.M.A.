@echo off
setlocal
REM Internal Setup pipeline (called from ..\DOGMA Setup.bat)
REM Skip GUI: set DOGMA_NO_WIZARD=1  (uses default installer_options)
REM Logs: mods\DOGMA\mo2\logs\
set "BUNDLE=%~dp0.."
call "%~dp0DOGMA (Setup Tools).bat" --log-reset %*
if errorlevel 1 goto :fail

set "SEL="
if /I "%DOGMA_NO_WIZARD%"=="1" goto :jobs
call "%~dp0run_job.bat" wizard %*
REM 2 = wizard cancelled — exit quietly (console stays hidden / closes)
if errorlevel 2 if not errorlevel 3 exit /b 2
if errorlevel 1 goto :fail
set "SEL=--use-selection"

:jobs
call "%~dp0run_job.bat" dependencies --tier all --mode reinstall %SEL% %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" disable --tier all %SEL% %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" defaults %SEL% %*
if errorlevel 1 goto :fail
call "%~dp0run_job.bat" validate --tier all --use-selection %*
if errorlevel 1 goto :fail
echo.
echo Logs: %BUNDLE%\logs\dogma_install.log
echo       %BUNDLE%\logs\dogma_report.log
exit /b 0

:fail
echo.
echo *** DOGMA Setup failed ***
echo Log: %BUNDLE%\logs\dogma_install.log
echo Report (if any): %BUNDLE%\logs\dogma_report.log
pause
exit /b 1
