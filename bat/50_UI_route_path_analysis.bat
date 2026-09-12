@echo off
setlocal EnableExtensions
set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"
set "PY=%ROOT_DIR%\runtime\python\python.exe"
"%PY%" "%ROOT_DIR%\src\50_Path_Analysis.py" --mode route %*
if errorlevel 1 pause
exit /b
