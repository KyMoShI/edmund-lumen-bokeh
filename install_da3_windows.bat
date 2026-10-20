@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 请先双击 run_windows.bat 完成基础安装。
  pause
  exit /b 1
)

echo 安装 Depth Anything 3（可选模型）...
echo.
echo 说明：depth-anything-3 必须用 --no-deps 安装，
echo       否则会把 numpy 降到 2 以下，并拖入 moviepy/pycolmap 等用不到的重依赖。
echo.

".venv\Scripts\python.exe" -m pip install --no-deps depth-anything-3==0.1.1
if errorlevel 1 (
  echo 深度模型依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)

echo.
echo 正在安装辅助依赖（omegaconf / einops / imageio / addict / torchvision）...
".venv\Scripts\python.exe" -m pip install -r requirements-da3.txt

REM pip 会因为 depth-anything-3 声明的可选依赖（pycolmap / xformers / numpy^<2）
REM 返回非零退出码，那是预期内的。这里用"能否 import"判断是否真的装好。
echo.
echo 验证依赖...
".venv\Scripts\python.exe" -c "import omegaconf, einops, imageio, addict, torchvision, depth_anything_3.cfg"
if errorlevel 1 (
  echo 依赖验证失败：上面一行报错里会写明缺少哪个包。
  pause
  exit /b 1
)

echo.
echo 安装完成。在软件右侧「AI 模型」里选择 DA3 Small / DA3 Base 即可，
echo 首次运行会自动下载权重（约 131 MB / 480 MB）。
pause
