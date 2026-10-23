@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\CodexUsageWidget.exe" (
  start "" "%~dp0dist\CodexUsageWidget.exe"
  exit /b 0
)
if exist "D:\python3.12\pythonw.exe" (
  start "" "D:\python3.12\pythonw.exe" "%~dp0main.py"
  exit /b 0
)
echo Build dist\CodexUsageWidget.exe or run main.py with Python 3.12.
pause
