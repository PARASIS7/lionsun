@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Creating a local Python environment...
py -3 -m venv .venv
if errorlevel 1 goto failed

echo Installing Pygame...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

:run
".venv\Scripts\python.exe" lion_sun_maze.py
if errorlevel 1 pause
exit /b %errorlevel%

:failed
echo Setup failed. Check your Python installation and internet connection.
pause
exit /b 1
