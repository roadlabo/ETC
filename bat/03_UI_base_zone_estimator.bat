@echo off
setlocal EnableExtensions

rem --- resolve repo root (this bat is in bat\) ---
set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "PY=%ROOT_DIR%\runtime\python\python.exe"

set "APP=%ROOT_DIR%\src\03_UI_base_zone_estimator.py"

set "LOGDIR=%ROOT_DIR%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\03_UI_base_zone_estimator_console.log"

rem --- launch (NO start/cmd; direct pythonw/python) ---
if exist "%PYW%" (
  "%PYW%" "%APP%" 1>>"%LOG%" 2>>&1
) else (
  "%PY%" "%APP%" 1>>"%LOG%" 2>>&1
)

exit /b %errorlevel%
