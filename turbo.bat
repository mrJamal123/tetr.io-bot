@echo off
cd /d "%~dp0"
py jogar.py --sem-especular
if errorlevel 1 pause
