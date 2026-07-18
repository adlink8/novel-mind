# Start NovelMind Cloudflare Tunnel (Windows)
# Prerequisites: FE :3005, BE :8010 running locally
$ErrorActionPreference = "Stop"
$cf = "$env:USERPROFILE\.cloudflared\bin\cloudflared.exe"
$config = "$env:USERPROFILE\.cloudflared\config.novelmind-win.yml"
if (-not (Test-Path $cf)) { throw "cloudflared not found: $cf" }
if (-not (Test-Path $config)) { throw "config not found: $config" }
Write-Host "Starting tunnel novelmind-win ..."
Write-Host "  https://novelmind.shuoyan.me      -> 127.0.0.1:3005"
Write-Host "  https://novelmind-api.shuoyan.me  -> 127.0.0.1:8010"
& $cf tunnel --config $config run novelmind-win
