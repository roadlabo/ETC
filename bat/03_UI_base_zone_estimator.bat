@echo off
setlocal

REM =====================================
REM Base Zone Estimator Launcher
REM =====================================

cd /d "%~dp0"

REM runtime python を優先使用
if exist "runtime\python.exe" (
    set PYTHON_EXE=runtime\python.exe
) else (
    set PYTHON_EXE=python
)

REM UI起動
"%PYTHON_EXE%" "03_UI_base_zone_estimator.py"

if errorlevel 1 (
    echo.
    echo エラーが発生しました。
    pause
)

endlocal
