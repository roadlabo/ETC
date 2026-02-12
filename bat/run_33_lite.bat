@echo off
setlocal
cd /d %~dp0\..

set "PY=%CD%\runtime\python\python.exe"
set "SRC=%CD%\src\33_branch_check.py"

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

"%PY%" "%SRC%" --nogui

pause
endlocal
