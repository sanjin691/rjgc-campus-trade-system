@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

echo.
echo   正在启动 校园二手商品交易系统 - 用户管理子系统...
echo.

python app.py --open
if errorlevel 1 (
  echo.
  echo   启动失败。请确认已安装 Python 3.10 或更高版本，并已加入 PATH。
  echo   也可以在项目目录手动运行：python app.py
  echo.
  pause
)

endlocal
