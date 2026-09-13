@echo off
setlocal EnableExtensions

cd /d "%~dp0.."
"runtime\python\python.exe" "src\14_area_builder.py" %*
if errorlevel 1 pause
exit /b
