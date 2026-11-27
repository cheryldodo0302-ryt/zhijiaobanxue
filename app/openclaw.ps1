param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)
. (Join-Path $PSScriptRoot 'openclaw_common.ps1')
$client = Get-ZhijiaoOpenClaw
Invoke-ZhijiaoOpenClaw -Client $client -Arguments $CommandArgs
exit $LASTEXITCODE
