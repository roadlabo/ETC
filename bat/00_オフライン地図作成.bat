@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "PY=%ROOT%\runtime\python\python.exe"
if not exist "%PY%" set "PY=python"

echo Create offline GSI pale map tiles.
echo Select a Polygon / MultiPolygon GeoJSON boundary file.
echo Output: %ROOT%\src\tiles\gsi_pale
echo.

set /p "BOUNDARY=GeoJSON path: "
set "BOUNDARY=!BOUNDARY:"=!"
if "!BOUNDARY!"=="" (
  echo ERROR: GeoJSON path is empty.
  pause
  exit /b 1
)

set /p "MIN_ZOOM=Min zoom [9]: "
if "!MIN_ZOOM!"=="" set "MIN_ZOOM=9"
set /p "MAX_ZOOM=Max zoom [18]: "
if "!MAX_ZOOM!"=="" set "MAX_ZOOM=18"
set /p "REUSE=Reuse tile folder (blank OK): "
set "REUSE=!REUSE:"=!"

if "!REUSE!"=="" (
  "%PY%" "%ROOT%\src\download_offline_tiles.py" "!BOUNDARY!" --output "%ROOT%\src\tiles\gsi_pale" --min-zoom !MIN_ZOOM! --max-zoom !MAX_ZOOM!
) else (
  "%PY%" "%ROOT%\src\download_offline_tiles.py" "!BOUNDARY!" --output "%ROOT%\src\tiles\gsi_pale" --min-zoom !MIN_ZOOM! --max-zoom !MAX_ZOOM! --reuse "!REUSE!"
)

pause
