@echo off
setlocal
chcp 65001 >nul
powershell.exe -STA -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\copy_openclaw_token.ps1"
set "OPENCLAW_COPY_EXIT=%ERRORLEVEL%"
pause
exit /b %OPENCLAW_COPY_EXIT%
