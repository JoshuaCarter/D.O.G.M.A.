@echo off
setlocal
REM DOGMA (Dependencies) — --tier required|suggested|all  --mode reinstall|ensure
REM required = features.*.requirements (incl. common); suggested = suggested:
call "%~dp0_run_job.bat" dependencies %*
exit /b %ERRORLEVEL%
