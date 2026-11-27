# Shared by the launcher and manual CLI entry. An existing system installation
# is unused; pinned binaries and machine-specific state stay in this project.
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = "1"
$openClawTemp = Join-Path $PSScriptRoot '.runtime\temp'
New-Item -ItemType Directory -Path $openClawTemp -Force | Out-Null
$env:TEMP = $openClawTemp
$env:TMP = $openClawTemp
$localBypass = @($env:NO_PROXY -split ',' | Where-Object { $_ }) + @('localhost', '127.0.0.1', '::1')
$env:NO_PROXY = ($localBypass | Select-Object -Unique) -join ','

function Get-ZhijiaoMcpPython {
    foreach ($relative in @('.venv-windows\Scripts\python.exe', '.venv\Scripts\python.exe')) {
        $candidate = Join-Path $PSScriptRoot $relative
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $candidate -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ('sqlalchemy','jwt','argon2','sklearn','requests','cryptography')) else 1)" *> $null
            $usable = $LASTEXITCODE -eq 0
        } catch { $usable = $false
        } finally { $ErrorActionPreference = $oldPreference }
        if ($usable) { return $candidate }
    }
    throw "未找到可用的项目 Python 环境，请先运行 app\start.ps1 -Mode setup。"
}

function Resolve-ZhijiaoOpenClawUser([string]$RequestedUserId) {
    if (-not [string]::IsNullOrWhiteSpace($RequestedUserId)) { return $RequestedUserId.Trim() }
    $bindingPath = Join-Path $PSScriptRoot 'openclaw.binding.json'
    if (Test-Path -LiteralPath $bindingPath) {
        $binding = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json
        if ($binding.user_id) { return [string]$binding.user_id }
    }
    return 'demo_teacher_001'
}

function Get-ZhijiaoPortableNode {
    $version = '24.18.0'
    $nodeDir = Join-Path $PSScriptRoot ".runtime\node\node-v$version-win-x64"
    $node = Join-Path $nodeDir 'node.exe'
    if (-not (Test-Path -LiteralPath $node)) {
        $installers = Join-Path (Split-Path -Parent $PSScriptRoot) 'installers'
        New-Item -ItemType Directory -Path $installers -Force | Out-Null
        $archive = Join-Path $installers "node-v$version-win-x64.zip"
        if (-not (Test-Path -LiteralPath $archive)) {
            Write-Host "首次运行：正在下载项目专用 Node.js，无需系统安装……"
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri "https://nodejs.org/dist/v$version/node-v$version-win-x64.zip" -OutFile $archive -UseBasicParsing -TimeoutSec 120
        }
        $expectedHash = '0ae68406b42d7725661da979b1403ec9926da205c6770827f33aac9d8f26e821'
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
            throw "Node.js 便携运行包校验失败，请重新复制 installers 目录。"
        }
        Write-Host "正在解压随附的 Node.js 便携运行环境……"
        Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $PSScriptRoot '.runtime\node') -Force
    }
    $env:PATH = $nodeDir + ';' + $env:PATH
    return $node
}

function Get-ZhijiaoOpenClaw([switch]$InstallIfMissing) {
    $node = Get-ZhijiaoPortableNode
    $localDir = Join-Path $PSScriptRoot '.runtime\openclaw-cli'
    $entry = Join-Path $localDir 'node_modules\openclaw\openclaw.mjs'
    & $node -e "const [m,n]=process.versions.node.split('.').map(Number);process.exit((m===24&&n>=16)||(m===26&&n>=1)||m>26?0:1)"
    if ($LASTEXITCODE -ne 0) { throw "Node.js 版本不兼容，请安装 Node.js 24.16+（24.x）或 26.1+。" }
    if (-not (Test-Path -LiteralPath $entry)) {
        if (-not $InstallIfMissing) { throw "未安装 OpenClaw，请双击仓库根目录的 启动OpenClaw.cmd 完成安装。" }
        $npm = Join-Path (Split-Path -Parent $node) 'node_modules\npm\bin\npm-cli.js'
        Write-Host "首次运行：正在项目目录安装 OpenClaw 2026.11.6……"
        & $node $npm install --prefix $localDir --cache (Join-Path $PSScriptRoot '.runtime\npm-cache') --no-audit --no-fund openclaw@2026.11.6 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "OpenClaw 安装失败，请检查网络或 npm 代理后重试。" }
    }
    # A copied folder gets a fresh machine/path-specific profile. Never reuse
    # another computer's device identity, locks or authored absolute paths.
    $identity = [Environment]::MachineName + '|' + ([IO.Path]::GetFullPath($PSScriptRoot)).ToLowerInvariant()
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $digest = $hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($identity)) }
    finally { $hasher.Dispose() }
    $profileId = ([BitConverter]::ToString($digest)).Replace('-', '').Substring(0, 12).ToLowerInvariant()
    $env:OPENCLAW_PROFILE = 'zhijiao-' + $profileId
    $env:OPENCLAW_STATE_DIR = Join-Path $PSScriptRoot ('.runtime\openclaw-state\' + $profileId)
    $env:OPENCLAW_CONFIG_PATH = Join-Path $env:OPENCLAW_STATE_DIR 'openclaw.json'
    return @{ Executable = $node; Prefix = @($entry); Portable = $true }
}

function Invoke-ZhijiaoOpenClaw {
    param([hashtable]$Client, [string[]]$Arguments)
    $prefix = @($Client.Prefix)
    & $Client.Executable @prefix @Arguments
}
