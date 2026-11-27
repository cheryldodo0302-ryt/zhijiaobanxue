param(
    [ValidateSet("all", "desktop", "api", "worker", "web-dev", "web-build", "test", "ai-check", "setup")]
    [string]$Mode = "all",
    [ValidateRange(0, 65535)]
    [int]$ApiPort = 0
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$runtimeTemp = Join-Path $project ".runtime\temp"
New-Item -ItemType Directory -Path $runtimeTemp -Force | Out-Null
# Some Windows Python/npm installations inherit a temporary directory that is
# not writable by the current user (especially when the project was launched
# from a sandboxed desktop app). Keep installer scratch files inside the
# project, where the launcher has already verified write access.
$env:TEMP = $runtimeTemp
$env:TMP = $runtimeTemp
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
if (-not $env:PIP_CACHE_DIR) { $env:PIP_CACHE_DIR = Join-Path $project ".runtime\pip-cache" }
if (-not $env:NPM_CONFIG_CACHE) { $env:NPM_CONFIG_CACHE = Join-Path $project ".runtime\npm-cache" }
$supportedVersionCheck = "import sys; assert sys.version_info[:2] in ((3, 10), (3, 11), (3, 12))"

if (Test-Path -LiteralPath (Join-Path $project "wheelhouse") -PathType Container) {
    $supportedVersionCheck = "import struct,sys; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8"
}

function Test-CompatiblePython([string]$Executable, [string[]]$Arguments = @()) {
    if (-not $Executable) { return $false }
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $Executable @Arguments -c $supportedVersionCheck *> $null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $oldPreference
    }
}

function New-PythonCandidate([string]$Executable, [string[]]$Arguments = @()) {
    [pscustomobject]@{ Executable = $Executable; Arguments = $Arguments }
}

function Find-CompatiblePython {
    $candidates = [System.Collections.Generic.List[object]]::new()
    $localPython = Join-Path $project ".runtime\python\python.exe"
    if (Test-Path -LiteralPath $localPython) { $candidates.Add((New-PythonCandidate $localPython)) }
    if ($env:ZHIJIAO_PYTHON) {
        $explicit = Get-Command $env:ZHIJIAO_PYTHON -ErrorAction SilentlyContinue
        if ($explicit) { $candidates.Add((New-PythonCandidate $explicit.Source)) }
        elseif (Test-Path -LiteralPath $env:ZHIJIAO_PYTHON) {
            $candidates.Add((New-PythonCandidate (Resolve-Path $env:ZHIJIAO_PYTHON).Path))
        }
    }
    if ($env:CONDA_PREFIX) {
        $condaPython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $condaPython) {
            $candidates.Add((New-PythonCandidate $condaPython))
        }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($selector in @("-3.12", "-3.11", "-3.10")) {
            $candidates.Add((New-PythonCandidate $launcher.Source @($selector)))
        }
        $candidates.Add((New-PythonCandidate $launcher.Source))
    }
    foreach ($commandName in @("python", "python3.12", "python3.11", "python3.10", "python3")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) { $candidates.Add((New-PythonCandidate $command.Source)) }
    }
    foreach ($candidate in $candidates) {
        if (Test-CompatiblePython $candidate.Executable $candidate.Arguments) {
            return $candidate
        }
    }
    $installer = Join-Path (Split-Path -Parent $project) "installers\python-3.12.10-amd64.exe"
    if (-not (Test-Path -LiteralPath $installer)) { throw "未找到 Python 及随附安装器，请完整复制 installers 目录。" }
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb") {
        throw "Python 安装器校验失败，请重新复制 installers 目录。"
    }
    $target = Join-Path $project ".runtime\python"
    Write-Host "首次运行：正在自动安装项目专用 Python，请稍候……"
    $install = Start-Process -FilePath $installer -ArgumentList @('/quiet', 'InstallAllUsers=0', ('TargetDir="' + $target + '"'), 'PrependPath=0', 'Include_launcher=0', 'Include_test=0', 'Include_pip=1', '/log', ('"' + (Join-Path $project '.runtime\python-install.log') + '"')) -PassThru -Wait -WindowStyle Hidden
    if ($install.ExitCode -notin @(0, 3010) -or -not (Test-CompatiblePython $localPython)) { throw "Python 自动安装失败，请查看 app\.runtime\python-install.log。" }
    return (New-PythonCandidate $localPython)
}

function Ensure-PythonEnvironment {
    $venvDir = Join-Path $project ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if ((Test-Path -LiteralPath $venvDir) -and -not (Test-Path -LiteralPath $venvPython) -and
        (Test-Path -LiteralPath (Join-Path $venvDir "bin\python"))) {
        $venvDir = Join-Path $project ".venv-windows"
        $venvPython = Join-Path $venvDir "Scripts\python.exe"
    }
    if (Test-Path -LiteralPath $venvPython) {
        if (-not (Test-CompatiblePython $venvPython)) {
            # A copied venv can retain the Python executable from another
            # computer. Preserve it and use a fresh local Windows venv instead
            # of failing before dependency installation even begins.
            $venvDir = Join-Path $project ".venv-windows"
            $venvPython = Join-Path $venvDir "Scripts\python.exe"
            if ((Test-Path -LiteralPath $venvPython) -and -not (Test-CompatiblePython $venvPython)) {
                # Keep the copied environment for recovery; build a local one.
                $resolvedVenv = [System.IO.Path]::GetFullPath($venvDir)
                $expectedVenv = [System.IO.Path]::GetFullPath((Join-Path $project ".venv-windows"))
                if ($resolvedVenv -ne $expectedVenv) { throw "虚拟环境路径不在项目目录内。" }
                $backupVenv = Join-Path $project (".venv-backup-" + [guid]::NewGuid().ToString("N"))
                Move-Item -LiteralPath $resolvedVenv -Destination $backupVenv
                Write-Host "已保留复制过来的失效环境，正在为本机重建……"
            }
        }
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $base = Find-CompatiblePython
        Write-Host "首次运行：正在用兼容 Python 创建项目虚拟环境……"
        New-Item -ItemType Directory -Path $venvDir -Force | Out-Null
        $baseArguments = @($base.Arguments)
        # Keep the project venv self-contained. A copied venv that depends on
        # D:\anapython is not deployable on another machine.
        & $base.Executable @baseArguments -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。请确认该 Python 安装包含 venv。" }
    }
    # Check imports without allowing a missing package to print a traceback to
    # PowerShell's native-command error stream.  PowerShell 7 can turn that
    # stderr output into a terminating NativeCommandError even when the
    # process only intends to return a non-zero probe status.
    $dependencyCheck = @'
import sys

modules = ('fastapi', 'faiss', 'sqlalchemy', 'sklearn', 'pypdf', 'docx', 'pptx', 'openpyxl', 'requests', 'jwt', 'argon2')
missing = []
for name in modules:
    try:
        __import__(name)
    except Exception:
        missing.append(name)
if missing:
    print(','.join(missing))
    sys.exit(1)
sys.exit(0)
'@
    $dependencyExit = 0
    $missingDependencies = @()
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $missingDependencies = @(& $venvPython -c $dependencyCheck 2>$null)
        $dependencyExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    if ($dependencyExit -ne 0) {
        $missingLabel = ($missingDependencies -join "").Trim()
        if ($missingLabel) { Write-Host "检测到缺少 Python 依赖：$missingLabel" }
        Write-Host "正在安装 Python 依赖……"
        $pipExit = 0
        $oldPipIndexUrl = $env:PIP_INDEX_URL
        if ([string]::IsNullOrWhiteSpace($oldPipIndexUrl)) {
            # Aliyun's public PyPI mirror is the default for faster first-time
            # setup in mainland China. An explicit PIP_INDEX_URL still wins.
            $env:PIP_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/"
        }
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            # Stream pip output so a slow download is visibly progressing. The
            # Write-Host sink keeps log lines from becoming function output.
            $wheelDir = Join-Path $PSScriptRoot "wheelhouse"
            $installArgs = @("-r", "requirements.txt")
            if (Test-Path -LiteralPath $wheelDir -PathType Container) {
                $installArgs = @("-r", "requirements-lock.txt", "--no-index", "--find-links", $wheelDir)
            }
            & $venvPython -m pip install --disable-pip-version-check --no-input --prefer-binary --retries 2 --timeout 30 @installArgs 2>&1 |
                ForEach-Object { Write-Host $_ }
            $pipExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $oldPreference
            if ([string]::IsNullOrWhiteSpace($oldPipIndexUrl)) {
                Remove-Item Env:PIP_INDEX_URL -ErrorAction SilentlyContinue
            } else {
                $env:PIP_INDEX_URL = $oldPipIndexUrl
            }
        }
        if ($pipExit -ne 0) {
            throw "Python 依赖安装失败（退出码 $pipExit）。请检查网络、代理、TEMP/TMP 权限、wheelhouse 完整性和 requirements-lock.txt。"
        }

        $missingAfterInstall = @()
        $postCheckExit = 0
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $missingAfterInstall = @(& $venvPython -c $dependencyCheck 2>&1)
            $postCheckExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $oldPreference
        }
        if ($postCheckExit -ne 0) {
            $missingLabel = ($missingAfterInstall -join "").Trim()
            throw "Python 依赖安装后仍不可用：$missingLabel。请删除项目 .venv 后重新安装。"
        }
    }
    return $venvPython
}

function Ensure-WebEnvironment {
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $node -or -not $npm) { throw "未找到 Node.js/npm，请安装 Node.js 20 或更高版本。" }
    & $node.Source -e "const m=Number(process.versions.node.split('.')[0]); if(m<20) process.exit(1)" *> $null
    if ($LASTEXITCODE -ne 0) { throw "Node.js 版本过低，请安装 Node.js 20 或更高版本。" }
    $webDir = Join-Path $project "web"
    $vueTsc = Join-Path $webDir "node_modules\.bin\vue-tsc.cmd"
    $nodeArch = ((& $node.Source -p "process.arch" 2>$null) -join "").Trim()
    $esbuildPackage = switch ($nodeArch) {
        "arm64" { "win32-arm64"; break }
        "ia32" { "win32-ia32"; break }
        default { "win32-x64" }
    }
    $esbuild = Join-Path $webDir "node_modules\@esbuild\$esbuildPackage\esbuild.exe"
    $webMarker = Join-Path $webDir "node_modules\.zhijiao-install-ok"
    if (-not (Test-Path -LiteralPath $webMarker) -or -not (Test-Path -LiteralPath $vueTsc) -or -not (Test-Path -LiteralPath $esbuild)) {
        Write-Host "正在安装前端依赖（将允许 vue-tsc/esbuild 必需的安装脚本）……"
        $oldIgnoreScripts = $env:NPM_CONFIG_IGNORE_SCRIPTS
        $oldFetchRetries = $env:NPM_CONFIG_FETCH_RETRIES
        $oldFetchTimeout = $env:NPM_CONFIG_FETCH_TIMEOUT
        $oldNpmRegistry = $env:NPM_CONFIG_REGISTRY
        $env:NPM_CONFIG_IGNORE_SCRIPTS = "false"
        $env:NPM_CONFIG_FETCH_RETRIES = "2"
        $env:NPM_CONFIG_FETCH_TIMEOUT = "60000"
        Push-Location $webDir
        try {
            # Honor a custom npm registry from the environment or npm config;
            # use npmmirror only when npm would otherwise use its public default.
            $configuredRegistry = ((& $npm.Source config get registry 2>$null | Select-Object -Last 1) -join "").Trim()
            if ([string]::IsNullOrWhiteSpace($oldNpmRegistry) -and
                $configuredRegistry.TrimEnd('/') -eq "https://registry.npmjs.org") {
                $env:NPM_CONFIG_REGISTRY = "https://registry.npmmirror.com/"
            }
            & $npm.Source ci --no-audit --no-fund --ignore-scripts=false
        }
        finally {
            Pop-Location
            if ($null -eq $oldIgnoreScripts) { Remove-Item Env:NPM_CONFIG_IGNORE_SCRIPTS -ErrorAction SilentlyContinue } else { $env:NPM_CONFIG_IGNORE_SCRIPTS = $oldIgnoreScripts }
            if ($null -eq $oldFetchRetries) { Remove-Item Env:NPM_CONFIG_FETCH_RETRIES -ErrorAction SilentlyContinue } else { $env:NPM_CONFIG_FETCH_RETRIES = $oldFetchRetries }
            if ($null -eq $oldFetchTimeout) { Remove-Item Env:NPM_CONFIG_FETCH_TIMEOUT -ErrorAction SilentlyContinue } else { $env:NPM_CONFIG_FETCH_TIMEOUT = $oldFetchTimeout }
            if ($null -eq $oldNpmRegistry) { Remove-Item Env:NPM_CONFIG_REGISTRY -ErrorAction SilentlyContinue } else { $env:NPM_CONFIG_REGISTRY = $oldNpmRegistry }
        }
        if ($LASTEXITCODE -ne 0) { throw "前端依赖安装失败（退出码 $LASTEXITCODE）。请检查网络、代理、npm 配置和 TEMP/TMP 权限。" }
    }
    if (-not (Test-Path -LiteralPath $esbuild)) {
        Write-Host "esbuild 二进制未生成，正在执行受控修复……"
        Push-Location $webDir
        try { & $npm.Source rebuild esbuild --foreground-scripts --ignore-scripts=false --dangerously-allow-all-scripts }
        finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "esbuild 修复失败（退出码 $LASTEXITCODE）。请检查 npm 安装脚本权限。" }
    }
    if (-not (Test-Path -LiteralPath $vueTsc) -or -not (Test-Path -LiteralPath $esbuild)) {
        throw "前端依赖安装后仍不完整：缺少 vue-tsc.cmd 或 $esbuild。请重新执行安装。"
    }
    New-Item -ItemType File -Path $webMarker -Force | Out-Null
    return $npm.Source
}

function Test-PortBindable([int]$Port) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback,
            $Port
        )
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $listener) { $listener.Stop() }
    }
}

function Resolve-ApiPort {
    if ($ApiPort -gt 0) { return $ApiPort }
    if ($env:ZHIJIAO_API_PORT) {
        $parsedPort = 0
        if (-not [int]::TryParse($env:ZHIJIAO_API_PORT, [ref]$parsedPort) -or
            $parsedPort -lt 1 -or $parsedPort -gt 65535) {
            throw "ZHIJIAO_API_PORT 必须是 1 到 65535 的整数。"
        }
        return $parsedPort
    }
    foreach ($candidate in @(8000) + (18001..18020)) {
        if (Test-PortBindable $candidate) {
            if ($candidate -ne 8000) {
                Write-Host "[API] 端口 8000 不可绑定，自动改用 $candidate。"
            }
            return $candidate
        }
    }
    throw "未找到可用的 API 端口（已尝试 8000 和 18001-18020）。"
}

if ($Mode -in @("web-dev", "web-build")) {
    $npm = Ensure-WebEnvironment
    Push-Location (Join-Path $project "web")
    try {
        if ($Mode -eq "web-dev") { & $npm run dev }
        else { & $npm run build }
    } finally { Pop-Location }
    exit $LASTEXITCODE
}

$pythonExe = Ensure-PythonEnvironment
if ($Mode -eq "all") { [void](Ensure-WebEnvironment) }
if ($Mode -in @("desktop", "setup")) {
    # A prepared folder runs without Node.js/npm on the destination computer.
    & $pythonExe scripts/desktop_assets.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "正在准备与当前源码匹配的桌面界面……"
        $npm = Ensure-WebEnvironment
        Push-Location (Join-Path $project "web")
        try { & $npm run build } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "前端构建失败。" }
        & $pythonExe scripts/desktop_assets.py --stamp
        if ($LASTEXITCODE -ne 0) { throw "前端构建校验失败。" }
    }
}
$selectedApiPort = $null
if ($Mode -eq "all" -and $ApiPort -gt 0) {
    $env:ZHIJIAO_API_PORT = $ApiPort.ToString()
}
if ($Mode -eq "api") {
    $selectedApiPort = Resolve-ApiPort
}
$version = & $pythonExe -c "import platform; print(platform.python_version())"
Write-Host "使用项目 Python：$pythonExe（$version）"
if ($Mode -in @("all", "desktop", "api", "worker", "setup")) {
    & $pythonExe job_secret_store.py
    if ($LASTEXITCODE -ne 0) { throw "已有 API 配置无法读取，请按上方提示补齐原电脑的配置文件。" }
}
if ($Mode -in @("all", "desktop", "api", "setup")) {
    & $pythonExe scripts/bootstrap_demo.py --if-empty
    if ($LASTEXITCODE -ne 0) { throw "初始化演示账号失败。" }
}

switch ($Mode) {
    "desktop" { & $pythonExe scripts/run_desktop.py }
    "all" { & $pythonExe scripts/run_all.py }
    "api" { & $pythonExe -m uvicorn api:app --host 127.0.0.1 --port $selectedApiPort }
    "worker" { & $pythonExe scripts/run_ingestion_worker.py }
    "test" { & $pythonExe -m pytest -q }
    "ai-check" { & $pythonExe qwen_check.py }
    "setup" { Write-Host "智教伴学运行环境已准备完成。" }
}
exit $LASTEXITCODE
