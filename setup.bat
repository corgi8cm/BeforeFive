@echo off
rem Create the virtual environment and install packages. The venv lives in %LOCALAPPDATA%\ReportTool\venv so OneDrive does not sync it.
set VENV=%LOCALAPPDATA%\ReportTool\venv
if not exist "%VENV%\Scripts\python.exe" python -m venv "%VENV%"
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
echo.
echo Setup complete. Run run.bat to start.
pause
