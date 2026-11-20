@echo off
cd /d "%~dp0"
py jogar.py --layout esquerda
if errorlevel 1 pause
