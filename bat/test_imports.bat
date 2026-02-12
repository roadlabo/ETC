@echo off
cd /d %~dp0\..

set PY=%CD%\runtime\python\python.exe

"%PY%" -c "import numpy, pandas, matplotlib, openpyxl, folium; print('OK imports')"
if errorlevel 1 (
  echo [ERROR] import failed
  pause
  exit /b 1
)

echo [OK] imports passed
pause
