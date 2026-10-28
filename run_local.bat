@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI知识库

echo ==================================================
echo   AI知识库 · 本地测试
echo   目录: %cd%
echo ==================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [X] 还没装环境。请先双击 install_windows.bat
  pause
  exit /b 1
)

echo [1/3] 清理旧进程（端口 8765）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8765 .*LISTENING"') do (
  echo     结束 PID %%p
  taskkill /F /PID %%p >nul 2>nul
)
ping -n 2 127.0.0.1 >nul 2>nul

echo [2/3] 启动服务 http://127.0.0.1:8765/
echo       改代码后关掉本窗口，再双击启动即可
echo       日志在下面，别关这个黑窗口
echo.
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

start "" cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:8765/"

echo [3/3] 运行中...
echo --------------------------------------------------
".venv\Scripts\python.exe" -m app --no-browser --port 8765
set ERR=%ERRORLEVEL%
echo.
echo --------------------------------------------------
if not "%ERR%"=="0" (
  echo [X] 服务异常退出，错误码 %ERR%
) else (
  echo 服务已退出。
)
echo 按任意键关闭窗口...
pause
exit /b %ERR%
