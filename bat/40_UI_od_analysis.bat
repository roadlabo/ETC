@echo off
setlocal EnableExtensions
set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"
"%ROOT_DIR%\runtime\python\python.exe" -X utf8 "%ROOT_DIR%\src\40_UI_od_analysis.py" %*
if errorlevel 1 pause
exit /b
