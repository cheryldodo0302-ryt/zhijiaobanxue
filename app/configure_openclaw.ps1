param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$UserId,
    [switch]$Replace
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到项目 Python：$python。请先运行 .\start.ps1 -Mode setup。"
}
$openclaw = Get-Command openclaw -ErrorAction SilentlyContinue
if (-not $openclaw) {
    throw "未找到 openclaw 命令。请先安装 OpenClaw，并确认 openclaw --version 可运行。"
}

$oldUserId = $env:ZHIJIAO_OPENCLAW_USER_ID
try {
    $env:ZHIJIAO_OPENCLAW_USER_ID = $UserId
    & $python -m integrations.openclaw_adapter.server --check
    if ($LASTEXITCODE -ne 0) { throw "数据库或绑定账号检查失败。" }
} finally {
    $env:ZHIJIAO_OPENCLAW_USER_ID = $oldUserId
}

if ($Replace) {
    & $openclaw.Source mcp remove zhijiao-database 2>$null
}

& $openclaw.Source mcp add zhijiao-database `
    --command $python `
    --arg -m `
    --arg integrations.openclaw_adapter.server `
    --cwd $project `
    --env "ZHIJIAO_OPENCLAW_USER_ID=$UserId"
if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw MCP 配置写入失败；若已有同名配置，请加 -Replace 后重试。"
}

& $openclaw.Source mcp doctor zhijiao-database --probe
if ($LASTEXITCODE -ne 0) { throw "配置已写入，但 OpenClaw 实时探测失败。" }
Write-Host "OpenClaw 已连接智教伴学数据库（zhijiao-database）。"
