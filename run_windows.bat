@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYEXE="
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3.11"
if not defined PYEXE (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
  echo 未找到 Python。请安装 Python 3.10-3.12，安装时勾选 "Add Python to PATH"。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 正在创建虚拟环境...
  %PYEXE% -m venv .venv
  if errorlevel 1 (
    echo 创建虚拟环境失败，请检查 Python 安装。
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import PySide6, cv2, numpy, PIL" >nul 2>&1
if errorlevel 1 (
  echo 正在安装依赖，首次运行需要联网，请稍候...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" "app\main.py"
if errorlevel 1 pause
