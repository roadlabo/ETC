@echo off
setlocal EnableExtensions

set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "PY=%ROOT_DIR%\runtime\python\python.exe"

set "APP=%ROOT_DIR%\src\11_UI_crossroad_sampler.py"

set "LOGDIR=%ROOT_DIR%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\11_crossroad_sampler_console_%RANDOM%.log"

echo ================================================== 1>>"%LOG%" 2>>&1
echo [%DATE% %TIME%] 11_crossroad_sampler launch 1>>"%LOG%" 2>>&1
echo ROOT_DIR="%ROOT_DIR%" 1>>"%LOG%" 2>>&1
echo APP="%APP%" 1>>"%LOG%" 2>>&1
echo PY="%PY%" 1>>"%LOG%" 2>>&1
echo PYW="%PYW%" 1>>"%LOG%" 2>>&1

if not exist "%APP%" (
  echo [ERROR] UI script not found: "%APP%" 1>>"%LOG%" 2>>&1
  echo UI script not found: "%APP%"
  pause
  exit /b 1
)

rem Use python.exe first so startup errors are captured in the log and visible on failure.
if exist "%PY%" (
  "%PY%" "%APP%" 1>>"%LOG%" 2>>&1
) else if exist "%PYW%" (
  "%PYW%" "%APP%" 1>>"%LOG%" 2>>&1
) else (
  echo [ERROR] Embedded Python runtime not found: "%PY%" 1>>"%LOG%" 2>>&1
  echo Embedded Python runtime not found: "%PY%"
  pause
  exit /b 2
)

set "EXIT_CODE=%errorlevel%"
echo [%DATE% %TIME%] exit code %EXIT_CODE% 1>>"%LOG%" 2>>&1
if not "%EXIT_CODE%"=="0" (
  echo.
  echo 11_crossroad_sampler exited with error code %EXIT_CODE%.
  echo Log: "%LOG%"
  pause
)

exit /b %EXIT_CODE%
