@echo off
setlocal
set "ROOT=%~dp0.."
set "PY=%ROOT%\runtime\python\python.exe"
if not exist "%PY%" set "PY=python"

echo 任意範囲の国土地理院 淡色地図タイルを作成します。
echo 対象範囲の Polygon / MultiPolygon GeoJSON を指定してください。
echo 保存先: %ROOT%\src\tiles\gsi_pale
echo.

set /p "BOUNDARY=GeoJSONファイルのパス: "
if "%BOUNDARY%"=="" (
  echo GeoJSONファイルが指定されていません。
  pause
  exit /b 1
)

set /p "MIN_ZOOM=最小ズーム [9]: "
if "%MIN_ZOOM%"=="" set "MIN_ZOOM=9"
set /p "MAX_ZOOM=最大ズーム [18]: "
if "%MAX_ZOOM%"=="" set "MAX_ZOOM=18"
set /p "REUSE=既存タイル再利用元フォルダー（空欄可）: "

if "%REUSE%"=="" (
  "%PY%" "%ROOT%\src\download_offline_tiles.py" "%BOUNDARY%" --output "%ROOT%\src\tiles\gsi_pale" --min-zoom %MIN_ZOOM% --max-zoom %MAX_ZOOM%
) else (
  "%PY%" "%ROOT%\src\download_offline_tiles.py" "%BOUNDARY%" --output "%ROOT%\src\tiles\gsi_pale" --min-zoom %MIN_ZOOM% --max-zoom %MAX_ZOOM% --reuse "%REUSE%"
)

pause
