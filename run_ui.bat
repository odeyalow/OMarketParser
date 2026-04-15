@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "VENV_PYTHON=%ROOT_DIR%.venv\Scripts\python.exe"
set "APP_PORT=5050"

if not exist "%VENV_PYTHON%" (
    echo Creating virtual environment...
    python -m venv "%ROOT_DIR%.venv"
    if errorlevel 1 exit /b 1
)

echo Installing dependencies...
"%VENV_PYTHON%" -m pip install -r "%ROOT_DIR%requirements.txt"
if errorlevel 1 exit /b 1

start "" powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:%APP_PORT%'"
set "PORT=%APP_PORT%"
"%VENV_PYTHON%" "%ROOT_DIR%web_app.py"
