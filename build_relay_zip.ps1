param(
    [string]$OutputName = "relay-deploy-linux-x64.zip",
    [ValidateSet("3.10", "3.11", "3.12")]
    [string]$PythonVersion = "3.10"
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildDir = [IO.Path]::GetFullPath((Join-Path $project ("relay_build_" + [guid]::NewGuid().ToString("N"))))
$outputPath = [IO.Path]::GetFullPath((Join-Path $project $OutputName))
$projectPrefix = [IO.Path]::GetFullPath($project).TrimEnd('\') + '\'

if (-not $buildDir.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe build directory: $buildDir"
}
if (-not $outputPath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe output path: $outputPath"
}

$pythonArgs = @()
$python = $null
if ($env:ZHIJIAO_PYTHON) {
    if (Test-Path -LiteralPath $env:ZHIJIAO_PYTHON) {
        $python = (Resolve-Path -LiteralPath $env:ZHIJIAO_PYTHON).Path
    } else {
        $command = Get-Command $env:ZHIJIAO_PYTHON -ErrorAction SilentlyContinue
        if ($command) { $python = $command.Source }
    }
}
if (-not $python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}
if (-not $python) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $python = $launcher.Source
        $pythonArgs = @("-$PythonVersion")
    }
}
if (-not $python) { throw "未找到 Python，请安装 Python $PythonVersion 或设置 ZHIJIAO_PYTHON。" }

if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Force
}
New-Item -ItemType Directory -Path $buildDir | Out-Null

Write-Host "Installing isolated relay dependencies..."
$pythonAbi = "cp" + $PythonVersion.Replace(".", "")
& $python @pythonArgs -m pip install `
    --disable-pip-version-check `
    --no-compile `
    --upgrade `
    --platform manylinux2014_x86_64 `
    --implementation cp `
    --python-version $PythonVersion `
    --abi $pythonAbi `
    --only-binary=:all: `
    --target $buildDir `
    -r (Join-Path $project "relay\requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Relay dependency installation failed."
}

Copy-Item -LiteralPath (Join-Path $project "relay\app.py") `
    -Destination (Join-Path $buildDir "app.py") -Force

# Windows PowerShell Compress-Archive can fail on mapped .pyd/.dll files.
# The inbox bsdtar creates a standard ZIP without opening those files as modules.
$tar = (Get-Command tar.exe -ErrorAction Stop).Source
& $tar -a -c -f $outputPath -C $buildDir .
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outputPath)) {
    throw "Relay ZIP creation failed."
}

$entries = & $tar -tf $outputPath
if ($LASTEXITCODE -ne 0 -or -not ($entries -match '(^|\./)app\.py$')) {
    throw "Relay ZIP verification failed: app.py is not at the archive root."
}
if ($entries -match '\.pyd$') {
    throw "Relay ZIP verification failed: Windows .pyd files were included."
}
if (-not ($entries -match '\.so$')) {
    throw "Relay ZIP verification failed: Linux native dependencies were not included."
}
if (-not ($entries -match '(^|\./)exceptiongroup/')) {
    throw "Relay ZIP verification failed: Python 3.10 exceptiongroup dependency is missing."
}

$sizeMb = [math]::Round((Get-Item -LiteralPath $outputPath).Length / 1MB, 2)
Write-Host "Relay package created for Linux x64 / Python ${PythonVersion}: $outputPath ($sizeMb MB)"
