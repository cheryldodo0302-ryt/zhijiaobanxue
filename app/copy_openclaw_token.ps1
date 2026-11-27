$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'openclaw_common.ps1')
$client = Get-ZhijiaoOpenClaw
if (-not (Test-Path -LiteralPath $env:OPENCLAW_CONFIG_PATH)) {
    throw '请先运行“启动OpenClaw.cmd”，再复制网关令牌。'
}
$config = Get-Content -LiteralPath $env:OPENCLAW_CONFIG_PATH -Raw -Encoding UTF8 | ConvertFrom-Json
$token = $config.gateway.auth.token
if ($config.gateway.auth.mode -ne 'token' -or $token -isnot [string] -or [string]::IsNullOrWhiteSpace($token)) {
    throw '当前本机配置没有可复制的网关令牌，请重新运行启动器。'
}
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Clipboard]::SetText($token, [Windows.Forms.TextDataFormat]::UnicodeText)
if ([Windows.Forms.Clipboard]::GetText([Windows.Forms.TextDataFormat]::UnicodeText) -ne $token) {
    throw '剪贴板校验失败，请检查剪贴板管理软件后重试。'
}
Write-Host '网关连接令牌已复制。' -ForegroundColor Green
Write-Host '回到浏览器，在令牌输入框按 Ctrl+V，然后点击“连接”。'
