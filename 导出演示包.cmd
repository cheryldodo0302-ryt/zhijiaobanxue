@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0app"
echo 请先关闭智教伴学启动窗口，再导出，以保留同一时刻的完整数据。
set "ZHIJIAO_EXPORT_PYTHON=.venv\Scripts\python.exe"
if exist ".venv-windows\Scripts\python.exe" set "ZHIJIAO_EXPORT_PYTHON=.venv-windows\Scripts\python.exe"
if not exist "%ZHIJIAO_EXPORT_PYTHON%" set "ZHIJIAO_EXPORT_PYTHON=python"
"%ZHIJIAO_EXPORT_PYTHON%" scripts\export_demo.py
if errorlevel 1 echo 导出失败，请查看上方错误信息。
pause
