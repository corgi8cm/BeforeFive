@echo off
chcp 65001 >nul
rem 不開黑色主控台視窗啟動（pythonw）
set VENV=%LOCALAPPDATA%\ReportTool\venv
if not exist "%VENV%\Scripts\pythonw.exe" (
    echo 找不到虛擬環境，請先執行 setup.bat
    pause
    exit /b 1
)
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0main.py"
