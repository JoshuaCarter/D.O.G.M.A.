@echo off
setlocal
REM DOGMA (Validate) — read-only fresh dogma_mo2_report.log
call "%~dp0_run_job.bat" validate %*
exit /b %ERRORLEVEL%
