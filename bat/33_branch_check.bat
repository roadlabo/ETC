@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "PYW=%ROOT_DIR%\runtime\python\pythonw.exe"
set "PY=%ROOT_DIR%\runtime\python\python.exe"

set "APP=%ROOT_DIR%\src\33_branch_check.py"

set "LOGDIR=%ROOT_DIR%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\33_branch_check_console.log"

echo [%date% %time%] 33_branch_check.bat RAW ARGS: %*>>"%LOG%"
echo [%date% %time%]   ARG1=%1>>"%LOG%"
echo [%date% %time%]   ARG2=%2>>"%LOG%"

rem ==========================================================
rem  Args normalize (robust):
rem    - Accept: 33_branch_check.bat --csv "path"
rem    - Accept legacy: 33_branch_check.bat "path"
rem  Build a single CSV_PATH (quotes removed), then re-quote once.
rem ==========================================================
set "CSV_PATH="

if /I "%~1"=="--csv" (
  set "CSV_PATH=%~2"
) else (
  if not "%~1"=="" (
    set "CSV_PATH=%~1"
  )
)

if not "%CSV_PATH%"=="" (
  echo [%date% %time%]   CSV_PATH=%CSV_PATH%>>"%LOG%"
) else (
  echo [%date% %time%]   CSV_PATH=(empty)>>"%LOG%"
)

rem --- launch ---
if exist "%PYW%" (
  if not "%CSV_PATH%"=="" (
    start "" /b cmd /c ""%PYW%" "%APP%" --csv "%CSV_PATH%" 1>>"%LOG%" 2>>&1"
  ) else (
    start "" /b cmd /c ""%PYW%" "%APP%" 1>>"%LOG%" 2>>&1"
  )
) else (
  if not "%CSV_PATH%"=="" (
    start "" /b cmd /c ""%PY%" "%APP%" --csv "%CSV_PATH%" 1>>"%LOG%" 2>>&1"
  ) else (
    start "" /b cmd /c ""%PY%" "%APP%" 1>>"%LOG%" 2>>&1"
  )
)

exit /b
