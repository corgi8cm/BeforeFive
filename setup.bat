@echo off
rem 建立虛擬環境並安裝套件。venv 放在 %LOCALAPPDATA%\ReportTool\venv，避免被 OneDrive 同步。
chcp 65001 >nul
set VENV=%LOCALAPPDATA%\ReportTool\venv
if not exist "%VENV%\Scripts\python.exe" python -m venv "%VENV%"
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
echo.
echo 安裝完成，執行 run.bat 啟動。
pause
