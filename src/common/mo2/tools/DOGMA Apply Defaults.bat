@echo off
setlocal
REM D.O.G.M.A. Apply Defaults
REM Logs: mods\DOGMA\mo2\logs\
call "%~dp0run_py.bat" defaults %*
exit /b %ERRORLEVEL%
