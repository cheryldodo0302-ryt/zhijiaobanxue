@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\install.ps1"
if not "%errorlevel%"=="0" (
    echo.
    echo 安装失败，请查看上方完整错误信息。
    pause
    exit /b %errorlevel%
)
echo.
pause
endlocal
