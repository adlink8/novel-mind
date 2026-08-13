# Start NovelMind Cloudflare Tunnel (Windows)
# Prerequisites: FE :3005, BE :8010 running locally
$ErrorActionPreference = "Stop"
$cf = "$env:USERPROFILE\.cloudflared\bin\cloudflared.exe"
$config = "$env:USERPROFILE\.cloudflared\config.novelmind-win.yml"
if (-not (Test-Path $cf)) { throw "cloudflared not found: $cf" }
if (-not (Test-Path $config)) { throw "config not found: $config" }
# 隧道入口域名取自本地 config（不硬编码到仓库）
Write-Host "Starting tunnel (config: $config) ..."
& $cf tunnel --config $config run novelmind-win
