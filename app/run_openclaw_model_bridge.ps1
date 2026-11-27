. (Join-Path $PSScriptRoot 'openclaw_common.ps1')
$client = Get-ZhijiaoOpenClaw
$python = Get-ZhijiaoMcpPython
& $python (Join-Path $PSScriptRoot 'scripts\run_openclaw_model_bridge.py') --config (Join-Path $env:OPENCLAW_STATE_DIR 'model-bridge.json')
exit $LASTEXITCODE
