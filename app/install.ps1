param()

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $project "start.ps1"

if (-not (Test-Path -LiteralPath $startScript)) {
    throw "未找到启动脚本：$startScript"
}

Write-Host "正在安装/修复智教伴学运行环境，请勿关闭窗口……"
& $startScript -Mode setup
if ($LASTEXITCODE -ne 0) {
    throw "智教伴学环境安装失败，退出码：$LASTEXITCODE"
}

Write-Host ""
Write-Host "安装完成。现在可以双击项目根目录的“智教伴学.exe”启动。" -ForegroundColor Green
