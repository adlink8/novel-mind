# Stop keep-alive supervisor (does not stop FE/BE unless -StopServices).
param(
    [switch]$StopServices
)
$ErrorActionPreference = "Continue"
$Root = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $Root "backend"))) {
    if (Test-Path "D:\ADLINK\Myproject\novel-mind\backend") {
        $Root = "D:\ADLINK\Myproject\novel-mind"
    } else {
        $Root = "C:\Users\li\Desktop\Myproject\novel-mind"
    }
}
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
