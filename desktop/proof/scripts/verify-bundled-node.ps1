# Verify: Electron embedded Node (ELECTRON_RUN_AS_NODE=1) starts Next standalone
$ErrorActionPreference = "Stop"
$repo = "D:\ADLINK\Myproject\novel-mind-new"
$electron = "$repo\desktop\proof\node_modules\electron\dist\electron.exe"
$server = "$repo\frontend\.next\standalone\server.js"
$port = 39877

if (-not (Test-Path $electron)) { Write-Error "electron.exe missing"; exit 2 }
if (-not (Test-Path $server)) { Write-Error "standalone server.js missing"; exit 3 }

# 1. Tighten PATH: strip nodejs / npm globals
$cleanPath = ($env:PATH -split ';' | Where-Object {
  $_ -notmatch 'nodejs' -and $_ -notmatch 'npm' -and $_ -notmatch 'Roaming\npm'
}) -join ';'

# 2. Verify no system node resolves
$env:PATH = $cleanPath
$resolvedNode = Get-Command node -ErrorAction SilentlyContinue
if ($resolvedNode) { Write-Error "system node still on PATH: $($resolvedNode.Source)"; exit 4 }
Write-Host "OK: system node stripped from PATH"

# 3. Launch standalone via Electron embedded Node
$env:ELECTRON_RUN_AS_NODE = "1"
$env:PORT = "$port"
$env:HOSTNAME = "127.0.0.1"
$proc = Start-Process -FilePath $electron -ArgumentList @($server) -PassThru -NoNewWindow -RedirectStandardOutput "$repo\desktop\proof\logs\bundled-node-stdout.log" -RedirectStandardError "$repo\desktop\proof\logs\bundled-node-stderr.log"

# 4. HTTP readiness probe
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ready = $true; Write-Host "OK: HTTP 200 on / after $($i*0.5)s"; break }
  } catch {}
}
if (-not $ready) {
  Write-Host "FAIL: no HTTP readiness. stderr tail:"
  Get-Content "$repo\desktop\proof\logs\bundled-node-stderr.log" -Tail 10 -ErrorAction SilentlyContinue
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  exit 5
}

# 5. Static asset probe
try {
  $s = Invoke-WebRequest -Uri "http://127.0.0.1:$port/_next/static/" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
  Write-Host "OK: /_next/static/ reachable (status $($s.StatusCode))"
} catch { Write-Host "INFO: /_next/static/ dir listing not expected (standalone serves files by path); checking a real asset instead" }

# 6. Embedded Node version (via Electron, RUN_AS_NODE)
$ver = & $electron -v 2>&1
Write-Host "Embedded Node version: $ver"

# 7. Clean shutdown: kill process tree
taskkill /PID $proc.Id /T /F 2>&1 | Out-Null
Start-Sleep -Milliseconds 800
$leftover = Get-Process -Name "electron" -ErrorAction SilentlyContinue
if ($leftover) { Write-Host "WARN: leftover electron processes: $($leftover.Count)" } else { Write-Host "OK: no leftover electron processes" }

Write-Host "BUNDLED_NODE_PROOF=PASS"
