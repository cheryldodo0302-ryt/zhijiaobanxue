param(
    [string]$UserId = "",
    [switch]$SkipDashboard,
    [switch]$ConfigureOnly
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot 'openclaw_common.ps1')
$UserId = Resolve-ZhijiaoOpenClawUser $UserId
$project = $PSScriptRoot
try { $python = Get-ZhijiaoMcpPython }
catch {
    Write-Host "正在准备智教伴学运行环境……"
    & (Join-Path $project 'start.ps1') -Mode setup
    if ($LASTEXITCODE -ne 0) { throw "智教伴学运行环境准备失败。" }
    $python = Get-ZhijiaoMcpPython
}

& (Join-Path $project 'configure_openclaw.ps1') -UserId $UserId -InstallOpenClaw
$client = Get-ZhijiaoOpenClaw

if ($client.Portable) {
    $savedConfig = Get-Content -LiteralPath $env:OPENCLAW_CONFIG_PATH -Raw | ConvertFrom-Json
    if (-not $savedConfig.gateway.mode) {
        Invoke-ZhijiaoOpenClaw -Client $client -Arguments @('setup', '--baseline', '--workspace', (Join-Path $project '.runtime\openclaw-workspace'))
        if ($LASTEXITCODE -ne 0) { throw "OpenClaw 本地初始化失败。" }
        Invoke-ZhijiaoOpenClaw -Client $client -Arguments @('config', 'set', 'gateway.mode', 'local')
        if ($LASTEXITCODE -ne 0) { throw "OpenClaw 本地 Gateway 配置失败。" }
    }
}
if ($ConfigureOnly) { Write-Host "OpenClaw 本机配置已准备完成。"; return }

$bridge = Get-Content -LiteralPath (Join-Path $env:OPENCLAW_STATE_DIR 'model-bridge.json') -Raw | ConvertFrom-Json
$bridgeUri = "http://127.0.0.1:$($bridge.port)/v1/models"
function Test-SharedModelBridge {
    try {
        $response = Invoke-WebRequest -Uri $bridgeUri -Headers @{ Authorization = ('Bearer ' + $bridge.token) } -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch { return $false }
}
if (-not (Test-SharedModelBridge)) {
    Write-Host "正在启动知识库共享模型入口……"
    $bridgeScript = Join-Path $project 'run_openclaw_model_bridge.ps1'
    $bridgeProcess = Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -PassThru `
        -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $bridgeScript + '"')) `
        -RedirectStandardOutput (Join-Path $env:OPENCLAW_STATE_DIR 'model-bridge.stdout.log') `
        -RedirectStandardError (Join-Path $env:OPENCLAW_STATE_DIR 'model-bridge.stderr.log')
    $bridgeDeadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $bridgeDeadline -and -not $bridgeProcess.HasExited -and -not (Test-SharedModelBridge)) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-SharedModelBridge)) { throw "共享模型入口未就绪，请查看本机配置目录中的 model-bridge.stderr.log。" }
}

$gatewayConfigPath = $env:OPENCLAW_CONFIG_PATH
if (-not $gatewayConfigPath) {
    $stateDir = $env:OPENCLAW_STATE_DIR
    if (-not $stateDir) { $stateDir = Join-Path $env:USERPROFILE '.openclaw' }
    $gatewayConfigPath = Join-Path $stateDir 'openclaw.json'
}
$gatewayConfig = Get-Content -LiteralPath $gatewayConfigPath -Raw | ConvertFrom-Json
if ($gatewayConfig.gateway.mode -eq 'remote') { throw "当前 OpenClaw 配置为远程 Gateway，本启动器只启动本机连接。" }
$port = 18789
if ($gatewayConfig.gateway.port) { $port = [int]$gatewayConfig.gateway.port }
$healthUrl = "http://127.0.0.1:$port/healthz"
$readyUrl = "http://127.0.0.1:$port/readyz"
function Test-LocalGateway {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch { return $false }
}
function Test-GatewayReady {
    try {
        $response = Invoke-WebRequest -Uri $readyUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch { return $false }
}

$gatewayProcess = $null
if (-not (Test-LocalGateway)) {
    Write-Host "正在启动本机 OpenClaw Gateway……"
    $runtime = Join-Path $project '.runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $gatewayScript = Join-Path $project 'run_openclaw_gateway.ps1'
    $gatewayProcess = Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -PassThru `
        -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $gatewayScript + '"')) `
        -RedirectStandardOutput (Join-Path $env:OPENCLAW_STATE_DIR 'gateway.stdout.log') `
        -RedirectStandardError (Join-Path $env:OPENCLAW_STATE_DIR 'gateway.stderr.log')
}
$deadline = (Get-Date).AddSeconds(240)
$nextProgress = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline -and -not (Test-GatewayReady)) {
    if ($gatewayProcess -and $gatewayProcess.HasExited) { break }
    if ((Get-Date) -ge $nextProgress) {
        Write-Host "OpenClaw 正在准备工具与对话服务，请稍候……"
        $nextProgress = (Get-Date).AddSeconds(15)
    }
    Start-Sleep -Milliseconds 500
}
if (-not (Test-GatewayReady)) {
    throw "Gateway 未就绪，请查看本机配置目录中的 gateway.stderr.log 和 gateway.stdout.log。"
}
# An HTTP listener alone is not proof of a healthy, authenticated Gateway.
Invoke-ZhijiaoOpenClaw -Client $client -Arguments @('gateway', 'health')
if ($LASTEXITCODE -ne 0) { throw "Gateway 鉴权或健康检查失败，请查看本地日志。" }

if (-not $SkipDashboard) {
    Invoke-ZhijiaoOpenClaw -Client $client -Arguments @('dashboard')
    if ($LASTEXITCODE -ne 0) { throw "OpenClaw Dashboard 打开失败。" }
}
Write-Host "OpenClaw 已启动，并已接入智教伴学（用户：$UserId）。"
Write-Host "对话与课程工具共用知识库 API 分析的模型及密钥；在知识库修改配置后，重新运行本启动器即可同步。"
