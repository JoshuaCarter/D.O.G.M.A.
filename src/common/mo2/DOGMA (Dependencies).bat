@echo off
setlocal
REM DOGMA (Dependencies) — --tier downloads|suggested|all  --mode reinstall|ensure
REM downloads = features.*.downloads (incl. common); suggested = suggested:
call "%~dp0_run_job.bat" dependencies %*
exit /b %ERRORLEVEL%
