@echo off
setlocal EnableExtensions

set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "PY=%ROOT_DIR%\runtime\python\python.exe"

set "APP=%ROOT_DIR%\src\11_UI_crossroad_sampler.py"

set "LOGDIR=%ROOT_DIR%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\11_crossroad_sampler_console.log"

if exist "%PYW%" (
  start "" /b cmd /c ""%PYW%" "%APP%" 1>>"%LOG%" 2>>&1"
) else (
  start "" /b cmd /c ""%PY%" "%APP%" 1>>"%LOG%" 2>>&1"
)

exit /b
