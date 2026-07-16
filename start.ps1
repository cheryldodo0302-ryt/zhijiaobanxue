param(
    [ValidateSet("ui", "api", "test", "ai-check")]
    [string]$Mode = "ui"
)

$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

$requiredImport = switch ($Mode) {
    "ui" { "streamlit, pypdf, docx, pptx, openpyxl, requests, certifi" }
    "api" { "uvicorn, fastapi, pypdf, docx, pptx, openpyxl, requests, certifi" }
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

if (-not $pythonExe) {
    throw "No Python runtime with the required module was found. Run: python -m pip install -r requirements.txt"
}

Write-Host "Using Python: $pythonExe"

switch ($Mode) {
    "ui" {
        & $pythonExe @pythonArgs -m streamlit run app.py
        break
    }
    "api" {
        & $pythonExe @pythonArgs -m uvicorn api:app --host 127.0.0.1 --port 8000
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
