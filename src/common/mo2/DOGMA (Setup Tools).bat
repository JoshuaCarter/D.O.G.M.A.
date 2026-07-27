@echo off
setlocal
REM DOGMA (Setup Tools) — ensure Python + PyYAML; check 7-Zip.
call "%~dp0run_job.bat" setup %*
exit /b %ERRORLEVEL%
