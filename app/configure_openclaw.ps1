param(
    [string]$UserId = "",
    [switch]$Replace,
    [switch]$CheckOnly,
    [switch]$InstallOpenClaw
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot 'openclaw_common.ps1')
$UserId = Resolve-ZhijiaoOpenClawUser $UserId
$python = Get-ZhijiaoMcpPython
$entry = Join-Path $PSScriptRoot 'scripts\run_openclaw_mcp.py'
Write-Host "正在验证智教伴学 MCP 握手、账号和课程读取……"
$rawProbe = & $python $entry --user-id $UserId --probe
if ($LASTEXITCODE -ne 0) { throw "MCP 检查失败，请确认账号存在、已启用并已修改初始密码。" }
$probe = ($rawProbe -join "`n") | ConvertFrom-Json
Write-Host "MCP 检查通过：$($probe.tool_count) 个工具，$($probe.course_count) 门可访问课程。"
$sharedSettingsScript = Join-Path $PSScriptRoot 'scripts\configure_openclaw_shared.py'
& $python $sharedSettingsScript --check-only --user-id $UserId --database $probe.database
if ($LASTEXITCODE -ne 0) {
    throw "请先用目标教师账号登录智教伴学，在知识库 API 分析设置中保存并测试配置。原 OpenClaw 绑定已保留。"
}
if ($CheckOnly) { return }

$client = Get-ZhijiaoOpenClaw -InstallIfMissing:$InstallOpenClaw
$register = Join-Path $PSScriptRoot 'scripts\register_openclaw.py'
$registerArgs = @('--executable', $client.Executable, '--python', $python,
                  '--user-id', $UserId, '--database', $probe.database)
foreach ($prefix in $client.Prefix) { $registerArgs += @('--prefix', $prefix) }
# Config patch updates this named server through its owning Gateway. The API
# preflight above must pass before changing either the MCP or model binding.
& $python $register @registerArgs
if ($LASTEXITCODE -ne 0) { throw "OpenClaw MCP 注册失败；原有其他 MCP 配置未修改。" }
& $python (Join-Path $PSScriptRoot 'scripts\configure_openclaw_shared.py') @registerArgs
if ($LASTEXITCODE -ne 0) { throw "知识库 API 配置同步失败，请先在知识库保存并测试 API 分析配置。" }
$bindingJson = @{ user_id = $UserId } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'openclaw.binding.json'), $bindingJson, (New-Object Text.UTF8Encoding($true)))
Write-Host "已注册 zhijiao-database（绑定账号：$UserId）。"
