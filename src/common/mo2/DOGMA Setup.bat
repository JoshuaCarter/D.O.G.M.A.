@echo off
setlocal
REM D.O.G.M.A. Setup — user-facing entry (implementation in tools\)
REM Skip GUI: set DOGMA_NO_WIZARD=1
REM Logs: mods\DOGMA\mo2\logs\
call "%~dp0tools\setup.bat" %*
exit /b %ERRORLEVEL%
