@echo off
setlocal EnableExtensions

set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "PY=%ROOT_DIR%\runtime\python\python.exe"
set "APP=%ROOT_DIR%\src\15_UI_area_screening.py"

set "LOGDIR=%ROOT_DIR%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\15_UI_area_screening_console.log"

if exist "%PYW%" (
  start "" /b cmd /c ""%PYW%" "%APP%" 1>>"%LOG%" 2>>&1"
) else (
  "%PY%" "%APP%" 1>>"%LOG%" 2>>&1
  if errorlevel 1 pause
)

exit /b
