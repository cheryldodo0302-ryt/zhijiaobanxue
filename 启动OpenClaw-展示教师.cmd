@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0app\launch_openclaw.ps1" (
    echo ERROR: app\launch_openclaw.ps1 was not found.
    echo Keep this launcher beside the complete app folder.
    pause
    exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\launch_openclaw.ps1" -UserId "demo_showcase_teacher_001" %*
set "OPENCLAW_EXIT=%ERRORLEVEL%"
echo.
if "%OPENCLAW_EXIT%"=="0" (
    echo OpenClaw is running. Continue in the opened browser.
    echo Closing this launcher will keep background services running.
) else (
    echo ERROR: Startup did not finish. Keep the messages above for troubleshooting.
)
pause
exit /b %OPENCLAW_EXIT%
