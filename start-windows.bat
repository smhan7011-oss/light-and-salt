@echo off
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 (
 echo Python 3.12 or newer is required.
 echo Install Python from https://www.python.org/downloads/windows/ then retry.
 pause
 exit /b 1
)
py -3 launch.py
if errorlevel 1 pause
