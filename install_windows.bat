@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo ==================================================
echo   Douyin Study - Install (first time only)
echo ==================================================

rem 1. Find Python 3.10-3.12 (3.13+ is missing some packages)
set PY=
for %%v in (3.12 3.11 3.10) do (
  if not defined PY (
    py -%%v -c "import sys" >nul 2>nul && set "PY=py -%%v"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo [X] No suitable Python found. This app needs Python 3.10, 3.11 or 3.12.
  for /f "delims=" %%i in ('python --version 2^>nul') do echo     Found: %%i ^(too new or too old^)
  echo     Please install Python 3.12 from https://www.python.org/downloads/
  echo     ^(scroll down to "Looking for a specific release"^). Keep your other versions.
  echo     IMPORTANT: tick "Add python.exe to PATH" during install, then run this again.
  pause
  exit /b 1
)
for /f "delims=" %%i in ('%PY% --version') do echo Using %%i

rem 2. Virtual environment (rebuild if wrong version)
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)" >nul 2>nul || (
    echo Old environment uses a wrong Python version, rebuilding...
    rmdir /s /q .venv
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  %PY% -m venv .venv || (echo [X] Failed to create venv & pause & exit /b 1)
) else (
  echo [1/3] Environment exists, continuing...
)
set VPY=.venv\Scripts\python
%VPY% -m pip install -q -U pip --timeout 30 >nul 2>nul || %VPY% -m pip install -q -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>nul

rem 3. Install components one by one - one failure does not block the others
echo [2/3] Installing components (a few minutes)...
set OKLIST=
set BADLIST=
set NBAD=0
call :step "Douyin reader (playwright)" "playwright>=1.45"
call :step "Speech to text (faster-whisper)" "faster-whisper>=1.0.3"
call :step "Image text recognition (rapidocr)" "rapidocr-onnxruntime>=1.3.24"

echo [3/3] Checking browser (Microsoft Edge is used by default)...
%VPY% -c "import playwright" >nul 2>nul || goto summary
set "PF86=%ProgramFiles(x86)%"
if exist "%PF86%\Microsoft\Edge\Application\msedge.exe" goto summary
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" goto summary
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" goto summary
echo No Edge/Chrome found, downloading Chromium...
%VPY% -m playwright install chromium

:summary

echo.
echo ==================================================
if defined OKLIST echo   OK:     !OKLIST!
if defined BADLIST echo   FAILED: !BADLIST!
echo ==================================================
if !NBAD! EQU 0 (
  echo [OK] Done. Double-click start_windows.bat to launch.
) else (
  echo [WARN] !NBAD! component^(s^) failed ^(usually a network problem^).
  echo     Just run this script again - it only installs what is missing.
  echo     If it still fails, send a screenshot of this window to the author.
)
pause
exit /b 0

:step
echo  - %~1
%VPY% -m pip install -q "%~2" --timeout 30 && (set "OKLIST=!OKLIST! [%~1]" & exit /b 0)
echo    Default source failed, retrying with Tsinghua mirror...
%VPY% -m pip install -q "%~2" -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30 && (set "OKLIST=!OKLIST! [%~1]" & exit /b 0)
set "BADLIST=!BADLIST! [%~1]"
set /a NBAD+=1
exit /b 0
