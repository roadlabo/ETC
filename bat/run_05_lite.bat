@echo off
setlocal
cd /d %~dp0\..

set "PY=%CD%\runtime\python\python.exe"
set "SRC=%CD%\src\05_route_mapper_simple.py"
set "PYDEPS=%CD%\runtime\pydeps"

if not exist "%PY%" (
  echo [ERROR] python not found: "%PY%"
  pause
  exit /b 1
)

if not exist "%SRC%" (
  echo [ERROR] script not found: "%SRC%"
  pause
  exit /b 1
)

if exist "%PYDEPS%" (
  if defined PYTHONPATH (
    set "PYTHONPATH=%PYDEPS%;%PYTHONPATH%"
  ) else (
    set "PYTHONPATH=%PYDEPS%"
  )
)

if "%~1"=="" (
  "%PY%" "%SRC%"
) else (
  "%PY%" "%SRC%" "%~1"
)

pause
endlocal
