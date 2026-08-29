param(
    [ValidateSet("all", "ui", "api", "worker", "web-dev", "web-build", "test", "ai-check")]
    [string]$Mode = "all"
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$supportedVersionCheck = "import sys; assert sys.version_info[:2] in ((3, 10), (3, 11), (3, 12))"

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
    # Compatibility fallback for the user's existing Anaconda layout. An
    # explicit ZHIJIAO_PYTHON value always takes priority.
    foreach ($knownPath in @("D:\anapython\python.exe")) {
        if (Test-Path -LiteralPath $knownPath) {
            $candidates.Add((New-PythonCandidate $knownPath))
        }
    }
    foreach ($candidate in $candidates) {
        if (Test-CompatiblePython $candidate.Executable $candidate.Arguments) {
            return $candidate
        }
    }
    throw "未找到兼容的 Python。请安装 Python 3.10、3.11 或 3.12，或设置 ZHIJIAO_PYTHON 指向 python.exe。"
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
            throw "现有虚拟环境不是 Python 3.10/3.11/3.12。请先重命名 .venv，或设置 ZHIJIAO_PYTHON 后重新启动。"
        }
    } else {
        $base = Find-CompatiblePython
        Write-Host "首次运行：正在用兼容 Python 创建项目虚拟环境……"
        New-Item -ItemType Directory -Path $venvDir -Force | Out-Null
        $baseArguments = @($base.Arguments)
        # Reuse packages already installed in a compatible Anaconda/Python
        # installation when available.  This keeps first launch usable on
        # offline machines while pip can still add anything genuinely absent.
        & $base.Executable @baseArguments -m venv --system-site-packages $venvDir
        if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。请确认该 Python 安装包含 venv。" }
    }
    # Check imports without allowing a missing package to print a traceback to
    # PowerShell's native-command error stream.  PowerShell 7 can turn that
    # stderr output into a terminating NativeCommandError even when the
    # process only intends to return a non-zero probe status.
    $dependencyCheck = @'
import sys

try:
    import fastapi, streamlit, sklearn, pypdf, docx, pptx, openpyxl, requests, jwt, argon2
except Exception:
    # Keep the probe quiet; the caller will install requirements on status 1.
    sys.exit(1)
sys.exit(0)
'@
    $dependencyExit = 0
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $venvPython -c $dependencyCheck 1>$null 2>$null
        $dependencyExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    if ($dependencyExit -ne 0) {
        Write-Host "首次运行：正在安装 Python 依赖……"
        $pipExit = 0
        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            # Capture pip's stdout/stderr so the function still returns only
            # the interpreter path (PowerShell otherwise folds every native
            # output line into `$pythonExe` below).
            $pipOutput = & $venvPython -m pip install -r requirements.txt 2>&1
            $pipExit = $LASTEXITCODE
            foreach ($line in $pipOutput) { Write-Host $line }
        } finally {
            $ErrorActionPreference = $oldPreference
        }
        if ($pipExit -ne 0) { throw "Python 依赖安装失败，请检查网络、代理和 requirements.txt。" }
    }
    return $venvPython
}

function Ensure-WebEnvironment {
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $node -or -not $npm) { throw "未找到 Node.js/npm，请安装 Node.js 20 或更高版本。" }
    & $node.Source -e "const m=Number(process.versions.node.split('.')[0]); if(m<20) process.exit(1)" *> $null
    if ($LASTEXITCODE -ne 0) { throw "Node.js 版本过低，请安装 Node.js 20 或更高版本。" }
    if (-not (Test-Path -LiteralPath (Join-Path $project "web\node_modules"))) {
        Write-Host "首次运行：正在安装前端依赖……"
        Push-Location (Join-Path $project "web")
        try { & $npm.Source ci --no-audit --no-fund }
        finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "前端依赖安装失败，请检查网络、代理和 npm 配置。" }
    }
    return $npm.Source
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
$version = & $pythonExe -c "import platform; print(platform.python_version())"
Write-Host "使用项目 Python：$pythonExe（$version）"
if ($Mode -in @("all", "ui", "api")) {
    & $pythonExe scripts/bootstrap_demo.py --if-empty
    if ($LASTEXITCODE -ne 0) { throw "初始化演示账号失败。" }
}

switch ($Mode) {
    "all" { & $pythonExe scripts/run_all.py }
    "ui" { & $pythonExe -m streamlit run app.py }
    "api" { & $pythonExe -m uvicorn api:app --host 127.0.0.1 --port 8000 }
    "worker" { & $pythonExe scripts/run_ingestion_worker.py }
    "test" { & $pythonExe -m pytest -q }
    "ai-check" { & $pythonExe qwen_check.py }
}
exit $LASTEXITCODE
