@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\launch_openclaw.ps1" %*
if errorlevel 1 pause
exit /b %errorlevel%
