param(
    [string]$UserId = "demo_teacher_001",
    [switch]$SkipDashboard
)

$ErrorActionPreference = "Stop"
$project = $PSScriptRoot
$openclaw = Get-Command openclaw -ErrorAction SilentlyContinue

if (-not $openclaw) {
    throw "未找到 OpenClaw。请先按官方说明安装，并确认 openclaw --version 可运行。"
}

Write-Host "正在准备智教伴学运行环境……"
& (Join-Path $project "start.ps1") -Mode setup
if ($LASTEXITCODE -ne 0) { throw "智教伴学运行环境准备失败。" }

Write-Host "正在把智教伴学数据库注册到 OpenClaw……"
& (Join-Path $project "configure_openclaw.ps1") -UserId $UserId -Replace
if ($LASTEXITCODE -ne 0) { throw "OpenClaw MCP 注册失败。" }

Write-Host "正在启动 OpenClaw Gateway……"
& $openclaw.Source gateway start
if ($LASTEXITCODE -ne 0) {
    Write-Host "OpenClaw 尚未完成初始化，将打开交互式设置。完成后请再次双击“启动OpenClaw.cmd”。"
    & $openclaw.Source
    exit $LASTEXITCODE
}

if (-not $SkipDashboard) {
    Write-Host "正在打开 OpenClaw Dashboard……"
    & $openclaw.Source dashboard
    if ($LASTEXITCODE -ne 0) { throw "OpenClaw Dashboard 打开失败。" }
}

Write-Host "OpenClaw 已启动，并已连接智教伴学（用户：$UserId）。"
