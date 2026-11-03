@echo off
rem Start without a console window (pythonw)
set VENV=%LOCALAPPDATA%\ReportTool\venv
if not exist "%VENV%\Scripts\pythonw.exe" (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0main.py"
