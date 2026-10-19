@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 请先双击 run_windows.bat 完成基础安装。
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements-ai.txt
if errorlevel 1 (
  echo AI 依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)

echo AI 依赖安装完成。首次运行模型时需要联网下载权重。
pause
