@echo off
setlocal
set "ROOT=%~dp0.."
set /p "BOUNDARY=津山市境界GeoJSONのパスを入力してください: "
set "REUSE=D:\02_都市基盤整備課資料作り\10_城西駐車場分析\outputs\01a04722-1d24-76e1-bb66-a2bad173f051\調査結果マップ_完全オフライン"
"%ROOT%\runtime\python\python.exe" "%ROOT%\src\download_tsuyama_tiles.py" "%BOUNDARY%" --reuse "%REUSE%"
pause
