@echo off
cd /d %~dp0

set PY=%CD%\runtime\python\python.exe
set SRC=%CD%\src\33_branch_check.py

"%PY%" "%SRC%" --nogui

pause
