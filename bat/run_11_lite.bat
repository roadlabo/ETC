@echo off
setlocal

REM ===================================================
REM CODEX: Define the project root (parent of this .bat)
REM ===================================================
cd /d %~dp0\..
set "BASE_DIR=%CD%"

REM ===================================================
REM CODEX: Path to the embedded Python runtime
REM (runtime/python がある前提)
REM ===================================================
set "PYTHON_EXE=%BASE_DIR%\runtime\python\python.exe"

REM ===================================================
REM CODEX: Path to the target Python script
REM (src/11_crossroad_sampler.py)
REM ===================================================
set "SCRIPT=%BASE_DIR%\src\11_crossroad_sampler.py"

REM 依存（pydeps）を読み込ませる
set "PYDEPS=%BASE_DIR%\runtime\pydeps"
if exist "%PYDEPS%" (
  if defined PYTHONPATH (
    set "PYTHONPATH=%PYDEPS%;%PYTHONPATH%"
  ) else (
    set "PYTHONPATH=%PYDEPS%"
  )
)

echo ================================================
echo      Crossroad Sampler (11) 起動
echo ================================================

REM ===================================================
REM CODEX: Check if Python runtime exists
REM ===================================================
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python runtime が見つかりませんでした:
    echo      %PYTHON_EXE%
    pause
    exit /b 1
)

REM ===================================================
REM CODEX: Check if 11_crossroad_sampler.py exists
REM ===================================================
if not exist "%SCRIPT%" (
    echo [ERROR] 11_crossroad_sampler.py が見つかりませんでした:
    echo      %SCRIPT%
    pause
    exit /b 1
)

REM ===================================================
REM CODEX: Run the Python script
REM   - %PYTHON_EXE% で実行
REM   - %SCRIPT% を引数に渡す
REM ===================================================
"%PYTHON_EXE%" "%SCRIPT%"

REM ===================================================
REM CODEX: Pause so the console stays open after run
REM (エラーも見えるようにする)
REM ===================================================
echo.
echo 完了しました。Enterで終了します
pause

endlocal
