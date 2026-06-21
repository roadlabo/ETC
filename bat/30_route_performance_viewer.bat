@echo off
setlocal EnableExtensions

rem --- resolve repo root (this bat is in bat\) ---
set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "PY=%ROOT_DIR%\runtime\python\python.exe"

set "APP=%ROOT_DIR%\src\30_route_performance_viewer.py"

set "LOGDIR=%ROOT_DIR%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\30_route_performance_viewer_console.log"

if not exist "%APP%" (
  echo [ERROR] Viewer script not found: "%APP%" 1>>"%LOG%" 2>>&1
  exit /b 1
)

cd /d "%ROOT_DIR%"

if exist "%PYW%" (
  "%PYW%" "%APP%" 1>>"%LOG%" 2>>&1
) else if exist "%PY%" (
  "%PY%" "%APP%" 1>>"%LOG%" 2>>&1
) else (
  echo [ERROR] Embedded Python runtime not found: "%PY%" 1>>"%LOG%" 2>>&1
  exit /b 2
)

exit /b %errorlevel%
