@echo off
setlocal
cd /d "%~dp0.."
if not exist "runtime\python\pythonw.exe" (
  echo Python runtime not found. Please run the setup first.
  pause
  exit /b 1
)
start "" "runtime\python\pythonw.exe" "src\00_launcher.py"
