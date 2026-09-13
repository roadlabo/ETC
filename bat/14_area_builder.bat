@echo off
setlocal EnableExtensions

cd /d "%~dp0.."
"runtime\python\python.exe" "src\14_area_builder.py" %*
set "RESULT=%errorlevel%"
if not "%RESULT%"=="0" if not "%ETC_LAUNCHER%"=="1" pause
exit /b %RESULT%
