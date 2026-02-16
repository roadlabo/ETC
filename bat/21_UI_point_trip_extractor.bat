@echo off
setlocal
cd /d %~dp0\..

set "PY=%CD%\runtime\python\python.exe"
set "SRC=%CD%\src\21_UI_point_trip_extractor.py"
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

REM 依存（pydeps）を読み込ませる
if exist "%PYDEPS%" (
  if defined PYTHONPATH (
    set "PYTHONPATH=%PYDEPS%;%PYTHONPATH%"
  ) else (
    set "PYTHONPATH=%PYDEPS%"
  )
)

REM Qt6（PyQt6）を確実に見つけさせる（WebEngine含む安定化）
set "QT6=%PYDEPS%\PyQt6\Qt6"
if exist "%QT6%\bin" (
  set "PATH=%QT6%\bin;%PATH%"
  set "QT_PLUGIN_PATH=%QT6%\plugins"
  set "QTWEBENGINEPROCESS_PATH=%QT6%\bin\QtWebEngineProcess.exe"
  set "QTWEBENGINE_RESOURCES_PATH=%QT6%\resources"
  set "QTWEBENGINE_LOCALES_PATH=%QT6%\translations\qtwebengine_locales"
)

"%PY%" "%SRC%"
pause
endlocal
