param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $OutputPath) { $OutputPath = Join-Path $repositoryRoot "Uninstall.exe" }
$source = Join-Path $PSScriptRoot "UninstallLauncher.cs"
$compilerCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $compiler) { throw "Windows .NET Framework C# compiler was not found." }
if (-not (Test-Path -LiteralPath $source)) { throw "Uninstaller source was not found: $source" }

& $compiler /nologo /target:exe /platform:anycpu /optimize+ "/out:$OutputPath" $source
if ($LASTEXITCODE -ne 0) { throw "Uninstaller compilation failed with exit code $LASTEXITCODE." }
Write-Host "Created: $OutputPath"
