@echo off
setlocal EnableExtensions

set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "PY=%ROOT_DIR%\runtime\python\python.exe"

set "APP=%ROOT_DIR%\src\33_branch_check.py"

set "LOGDIR=%ROOT_DIR%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\33_branch_check_console.log"

rem --- normalize args (backward compatible) ---
set "ARGS=%*"
if not "%~1"=="" (
  set "FIRST=%~1"
  rem if first arg is not an option, treat it as CSV path
  if not "%FIRST:~0,2%"=="--" (
    set "ARGS=--csv ""%~1"""
  )
)

if exist "%PYW%" (
  start "" /b cmd /c ""%PYW%" "%APP%" %ARGS% 1>>"%LOG%" 2>>&1"
) else (
  start "" /b cmd /c ""%PY%" "%APP%" %ARGS% 1>>"%LOG%" 2>>&1"
)

exit /b
