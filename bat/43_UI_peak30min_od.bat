@echo off
setlocal EnableExtensions

set "BAT_DIR=%~dp0"
for %%I in ("%BAT_DIR%..") do set "ROOT_DIR=%%~fI"

set "APP=%ROOT_DIR%\src\43_UI_peak30min_od.py"
set "PY_RUNTIME=%ROOT_DIR%\runtime\python\python.exe"

if not exist "%APP%" (
  echo [ERROR] UI script not found: "%APP%"
  pause
  exit /b 1
)

if exist "%PY_RUNTIME%" (
  "%PY_RUNTIME%" "%APP%"
  exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%APP%"
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  python "%APP%"
  exit /b %errorlevel%
)

echo [ERROR] Python runtime not found. Install Python or bundle runtime\python\python.exe
pause
exit /b 2
