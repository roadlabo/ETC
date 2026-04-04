@echo off
setlocal EnableExtensions

set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "APP=%ROOT_DIR%\src\03_UI_base_zone_estimator.py"

cd /d "%ROOT_DIR%"
start "" "%PYW%" "%APP%"
exit /b 0
