@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "VENV_PYTHON=%ROOT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo Creating virtual environment...
    python -m venv "%ROOT_DIR%.venv"
    if errorlevel 1 exit /b 1
)

echo Installing dependencies...
"%VENV_PYTHON%" -m pip install -r "%ROOT_DIR%requirements.txt"
if errorlevel 1 exit /b 1

"%VENV_PYTHON%" "%ROOT_DIR%omarket_parser.py" %*
