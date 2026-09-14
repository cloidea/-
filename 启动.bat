@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo 未找到程序运行环境，请先安装依赖。
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "app.py"
