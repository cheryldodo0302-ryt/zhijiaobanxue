param(
    [ValidateSet("all", "ui", "api", "worker", "web-dev", "web-build", "test", "ai-check")]
    [string]$Mode = "all"
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

$requiredImport = switch ($Mode) {
    "all" { "uvicorn, fastapi, pypdf, docx, pptx, openpyxl, requests, certifi, jwt, argon2" }
    "ui" { "streamlit, pypdf, docx, pptx, openpyxl, requests, certifi" }
    "api" { "uvicorn, fastapi, pypdf, docx, pptx, openpyxl, requests, certifi, jwt, argon2" }
    "worker" { "pypdf, docx, pptx, openpyxl, requests" }
    "web-dev" { "" }
    "web-build" { "" }
    "test" { "pytest" }
    "ai-check" { "requests, certifi" }
}

$candidates = @()
$codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path $codexPython) {
    $candidates += ,@($codexPython, @())
}
$projectPython = "D:\anapython\python.exe"
if (Test-Path $projectPython) {
    $candidates += ,@($projectPython, @())
}
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $candidates += ,@($python.Source, @())
}
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    $candidates += ,@($launcher.Source, @("-3"))
}
$pythonExe = $null
$pythonArgs = @()
foreach ($candidate in $candidates) {
    $candidateExe = $candidate[0]
    $candidateArgs = $candidate[1]
    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $candidateExe @candidateArgs -c "import $requiredImport" 2>$null
    $probeExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorPreference
    if ($probeExitCode -eq 0) {
        $pythonExe = $candidateExe
        $pythonArgs = $candidateArgs
        break
    }
}
if ($Mode -in @("web-dev", "web-build")) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { throw "Node.js/npm is not installed or is not available in PATH." }
    Push-Location (Join-Path $project "web")
    try {
        if ($Mode -eq "web-dev") { & $npm.Source run dev }
        else { & $npm.Source run build }
    } finally { Pop-Location }
    exit $LASTEXITCODE
}

if (-not $pythonExe) {
    throw "No Python runtime with the required module was found. Run: python -m pip install -r requirements.txt"
}

Write-Host "Using Python: $pythonExe"

switch ($Mode) {
    "all" {
        & $pythonExe @pythonArgs scripts/run_all.py
        break
    }
    "ui" {
        & $pythonExe @pythonArgs -m streamlit run app.py
        break
    }
    "api" {
        & $pythonExe @pythonArgs -m uvicorn api:app --host 127.0.0.1 --port 8000
        break
    }
    "worker" {
        & $pythonExe @pythonArgs scripts/run_ingestion_worker.py
        break
    }
    "test" {
        & $pythonExe @pythonArgs -m pytest -q
        break
    }
    "ai-check" {
        & $pythonExe @pythonArgs qwen_check.py
        if ($LASTEXITCODE -ne 0) {
            throw "AI backend connectivity check failed."
        }
        break
    }
}
