@echo off
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
  echo Please run install_windows.bat first.
  pause
  exit /b 1
)
set PYTHONUTF8=1
.venv\Scripts\python -m app
pause
