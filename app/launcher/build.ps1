param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$launcherName = [string][char]0x667A + [char]0x6559 + [char]0x4F34 + [char]0x5B66 + ".exe"
if (-not $OutputPath) {
    $repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $OutputPath = Join-Path $repositoryRoot $launcherName
}
$source = Join-Path $PSScriptRoot "ZhijiaoLauncher.cs"
$icon = Join-Path $PSScriptRoot "app-icon.ico"
$compilerCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $compiler) {
    throw "Windows .NET Framework C# compiler was not found."
}
if (-not (Test-Path -LiteralPath $source)) {
    throw "Launcher source was not found: $source"
}
if (-not (Test-Path -LiteralPath $icon)) {
    throw "Launcher icon was not found: $icon"
}

& $compiler /nologo /target:exe /platform:anycpu /optimize+ "/win32icon:$icon" "/out:$OutputPath" $source
if ($LASTEXITCODE -ne 0) {
    throw "Launcher compilation failed with exit code $LASTEXITCODE."
}

Write-Host "Created: $OutputPath"
