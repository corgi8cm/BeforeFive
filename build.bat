@echo off
rem 打包成單一 exe：dist\ReportTool.exe。暫存檔放 %LOCALAPPDATA%，避免被 OneDrive 同步。
chcp 65001 >nul
set VENV=%LOCALAPPDATA%\ReportTool\venv
set WORK=%LOCALAPPDATA%\ReportTool\build
if not exist "%VENV%\Scripts\python.exe" (
    echo 找不到虛擬環境，請先執行 setup.bat
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install "pyinstaller>=6"
if not exist "%WORK%" mkdir "%WORK%"
"%VENV%\Scripts\python.exe" "%~dp0tools\make_ico.py" "%WORK%\icon.ico" || goto :fail
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name ReportTool --icon "%WORK%\icon.ico" ^
    --workpath "%WORK%" --specpath "%WORK%" --distpath "%~dp0dist" ^
    --exclude-module pytest --exclude-module tkinter ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtCharts --exclude-module PySide6.QtPdf ^
    "%~dp0main.py" || goto :fail
echo.
echo 完成：%~dp0dist\ReportTool.exe
echo 第一次執行會在 exe 旁邊建立 config\ 資料夾。
pause
exit /b 0
:fail
echo 打包失敗
pause
exit /b 1
