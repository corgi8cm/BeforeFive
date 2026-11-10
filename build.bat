@echo off
rem Build a single-file exe: dist\BeforeFive.exe. Temp files go to %LOCALAPPDATA% so OneDrive does not sync them.
set VENV=%LOCALAPPDATA%\ReportTool\venv
set WORK=%LOCALAPPDATA%\ReportTool\build
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
"%VENV%\Scripts\python.exe" -m pip install "pyinstaller>=6"
if not exist "%WORK%" mkdir "%WORK%"
"%VENV%\Scripts\python.exe" "%~dp0tools\make_ico.py" "%WORK%\icon.ico" || goto :fail
"%VENV%\Scripts\python.exe" "%~dp0tools\make_version_file.py" "%WORK%\version.txt" || goto :fail
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name BeforeFive --icon "%WORK%\icon.ico" --version-file "%WORK%\version.txt" ^
    --workpath "%WORK%" --specpath "%WORK%" --distpath "%~dp0dist" ^
    --exclude-module pytest --exclude-module tkinter ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtCharts --exclude-module PySide6.QtPdf ^
    "%~dp0main.py" || goto :fail
echo.
echo Done: %~dp0dist\BeforeFive.exe
echo On first run, a config\ folder is created next to the exe.
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
