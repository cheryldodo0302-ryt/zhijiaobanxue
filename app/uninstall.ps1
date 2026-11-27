param(
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$project = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path)).TrimEnd('\')
$projectName = Split-Path -Leaf $project

function Get-RemovalTargets {
    $targets = [System.Collections.Generic.List[string]]::new()
    foreach ($relative in @(
        ".venv",
        ".venv-windows",
        ".venv-local",
        ".runtime",
        "web\node_modules",
        "web\dist",
        "web\.vite",
        "web\.cache",
        ".pytest_cache"
    )) {
        $targets.Add((Join-Path $project $relative))
    }

    return $targets | Select-Object -Unique
}

function Get-ProjectProcesses {
    $processes = @()
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    } catch {
        try { $processes = @(Get-WmiObject Win32_Process -ErrorAction Stop) } catch { return @() }
    }
    $needle = $project.TrimEnd('\')
    return $processes | Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine.IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $_.Name -match '^(python|pythonw|node|nodejs|powershell|pwsh)(\.exe)?$'
    }
}

function Stop-ProjectProcesses {
    $processes = @(Get-ProjectProcesses)
    foreach ($item in $processes) {
        try {
            Write-Host "正在停止 $($item.Name) (PID $($item.ProcessId))……"
            Stop-Process -Id ([int]$item.ProcessId) -Force -ErrorAction Stop
        } catch {
            Write-Warning "无法停止 PID $($item.ProcessId)：$($_.Exception.Message)"
        }
    }
    if ($processes.Count -gt 0) { Start-Sleep -Milliseconds 800 }
}

function Remove-Target([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return $true
        } catch {
            if ($attempt -eq 3) {
                Write-Warning "删除失败：$Path`n$($_.Exception.Message)"
                return $false
            }
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

$targets = @(Get-RemovalTargets)
Write-Host "智教伴学环境卸载器"
Write-Host "项目目录：$project"
Write-Host ""
Write-Host "将删除以下运行环境/构建目录："
foreach ($target in $targets) {
    if (Test-Path -LiteralPath $target) { Write-Host "  $target" }
}
Write-Host ""
Write-Host "不会删除：源码、数据库、课程资料、上传文件、.env 配置和 Uninstall.exe。" -ForegroundColor Yellow

if (-not $Yes) {
    $answer = Read-Host "确认卸载请输入 YES，输入其他内容取消"
    if ($answer -cne "YES") {
        Write-Host "已取消卸载。"
        exit 0
    }
}

Stop-ProjectProcesses
$failed = [System.Collections.Generic.List[string]]::new()
foreach ($target in $targets) {
    if (-not (Remove-Target $target)) { $failed.Add($target) }
}

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Error ("卸载未完全完成，以下目录仍存在：" + ($failed -join "; "))
    exit 1
}

Write-Host ""
Write-Host "智教伴学运行环境已卸载。源码和用户数据已保留。" -ForegroundColor Green
