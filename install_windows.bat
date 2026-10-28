@echo off
cd /d "%~dp0"
echo ==================================================
echo   Douyin Study - Install (first time only)
echo ==================================================
set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" (
  echo [X] Python not found.
  echo     Please install Python 3.11 or 3.12 from https://www.python.org/downloads/
  echo     IMPORTANT: tick "Add python.exe to PATH" during install, then run this again.
  pause
  exit /b 1
)
echo [1/3] Creating virtual environment...
%PY% -m venv .venv || (echo [X] Failed to create venv & pause & exit /b 1)
echo [2/3] Installing packages (a few minutes)...
.venv\Scripts\python -m pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo Mirror failed, trying default PyPI...
  .venv\Scripts\python -m pip install -r requirements.txt || (echo [X] Install failed & pause & exit /b 1)
)
echo [3/3] Checking browser (Microsoft Edge is used by default)...
if not exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" if not exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" if not exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  echo No Edge/Chrome found, downloading Chromium...
  .venv\Scripts\python -m playwright install chromium
)
echo.
echo [OK] Done! Double-click start_windows.bat to launch.
pause
