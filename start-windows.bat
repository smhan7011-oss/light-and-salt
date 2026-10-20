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
if not exist ".venv\Scripts\python.exe" (
 py -3 -m venv .venv
 if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
.venv\Scripts\python.exe server.py --open
if errorlevel 1 goto fail
exit /b 0
:fail
echo Setup or startup failed. Keep this message for troubleshooting.
pause
exit /b 1
