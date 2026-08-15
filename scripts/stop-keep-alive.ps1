# Stop keep-alive supervisor (does not stop FE/BE unless -StopServices).
param(
    [switch]$StopServices
)
$ErrorActionPreference = "Continue"
# 只从脚本所在位置推导仓库根目录（已移除硬编码本机路径，仓库公开可读）
$Root = Split-Path $PSScriptRoot -Parent
$PidFile = Join-Path $PSScriptRoot "keep-alive.pid"
if (Test-Path $PidFile) {
    $id = (Get-Content $PidFile | Select-Object -First 1) -as [int]
    if ($id) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped keep-alive PID $id"
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine -match 'keep-alive\.ps1'
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped PID $($_.ProcessId)"
}

if ($StopServices) {
    Get-NetTCPConnection -LocalPort 8010,3005 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped listener PID $($_.OwningProcess) on port $($_.LocalPort)"
    }
}
