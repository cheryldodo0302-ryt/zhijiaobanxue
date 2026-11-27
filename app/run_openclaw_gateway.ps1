. (Join-Path $PSScriptRoot 'openclaw_common.ps1')
$client = Get-ZhijiaoOpenClaw
Invoke-ZhijiaoOpenClaw -Client $client -Arguments @('gateway', 'run', '--bind', 'loopback')
exit $LASTEXITCODE
