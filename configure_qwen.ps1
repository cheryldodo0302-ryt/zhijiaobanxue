$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverEnvPath = Join-Path $project "server.env"
$baseUrl = "https://ws-c4qflt1k6x8xwd4f.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
$model = "qwen-plus"

Write-Host "Configure the server-side Alibaba Cloud Model Studio key."
Write-Host "This key is stored in your Windows user environment and is never sent to the browser."
$secureKey = Read-Host "DASHSCOPE_API_KEY" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)

try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if (-not $plainKey.StartsWith("sk-")) {
        throw "The API key format is invalid."
    }
    [Environment]::SetEnvironmentVariable("DASHSCOPE_API_KEY", $plainKey, "User")
    [Environment]::SetEnvironmentVariable("ZHIJIAO_AI_PROVIDER", "qwen", "User")
    [Environment]::SetEnvironmentVariable("ZHIJIAO_AI_MODEL", $model, "User")
    [Environment]::SetEnvironmentVariable(
        "ZHIJIAO_AI_BASE_URL",
        $baseUrl,
        "User"
    )
    @(
        "DASHSCOPE_API_KEY=$plainKey"
        "ZHIJIAO_AI_PROVIDER=qwen"
        "ZHIJIAO_AI_BASE_URL=$baseUrl"
        "ZHIJIAO_AI_MODEL=$model"
    ) | Set-Content -LiteralPath $serverEnvPath -Encoding UTF8

    $acl = Get-Acl -LiteralPath $serverEnvPath
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
        "FullControl",
        "Allow"
    )
    $acl.SetAccessRule($rule)
    Set-Acl -LiteralPath $serverEnvPath -AclObject $acl
} finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $plainKey = $null
}

Write-Host "Qwen workspace backend configured in server.env."
Write-Host "Running a real connectivity check..."
& (Join-Path $project "start.ps1") ai-check
if ($LASTEXITCODE -ne 0) {
    throw "Qwen configuration was saved, but the real API connectivity check failed."
}
Write-Host "Configuration complete. Run .\start.ps1 ui"
