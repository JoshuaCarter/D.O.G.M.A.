@echo off
setlocal
REM DOGMA (Dependencies) — --tier downloads|suggested|all  --mode reinstall|ensure
REM downloads = feature depends: packs from mods.yml; suggested = mods.yml wizard:
call "%~dp0run_job.bat" dependencies %*
exit /b %ERRORLEVEL%
