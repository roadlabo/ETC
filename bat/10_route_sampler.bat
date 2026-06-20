@echo off
setlocal EnableExtensions

set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "PY=%ROOT_DIR%\runtime\python\python.exe"
set "APP=%ROOT_DIR%\src\10_UI_route_sampler.py"

set "LOGDIR=%ROOT_DIR%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\10_route_sampler_console.log"

if not exist "%APP%" (
  echo [ERROR] UI script not found: "%APP%" 1>>"%LOG%" 2>>&1
  exit /b 1
)

rem --- launch (NO start/cmd; direct pythonw/python) ---
if exist "%PYW%" (
  "%PYW%" "%APP%" 1>>"%LOG%" 2>>&1
) else if exist "%PY%" (
  "%PY%" "%APP%" 1>>"%LOG%" 2>>&1
) else (
  echo [ERROR] Embedded Python runtime not found: "%PY%" 1>>"%LOG%" 2>>&1
  exit /b 2
)

exit /b %errorlevel%
