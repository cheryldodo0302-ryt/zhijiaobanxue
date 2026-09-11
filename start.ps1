param(
    [ValidateSet("all", "ui", "api", "worker", "web-dev", "web-build", "test", "ai-check")]
    [string]$Mode = "all"
)

$ErrorActionPreference = "Stop"
# Keep the old root command usable while sharing one current implementation.
if ($Mode -eq "ui") { $Mode = "all" }
& (Join-Path $PSScriptRoot "zhijiao_banxue/start.ps1") -Mode $Mode
