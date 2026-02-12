@echo off
cd /d %~dp0

set PY=%CD%\runtime\python\python.exe
set SRC=%CD%\src\05_route_mapper_simple.py

"%PY%" "%SRC%" --nogui

pause
