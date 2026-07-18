# Stop novelmind-win cloudflared processes started with our config
Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
  Where-Object { $_.CommandLine -match 'novelmind-win|config.novelmind-win' } |
  ForEach-Object {
    Write-Host "kill $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Write-Host "done"
