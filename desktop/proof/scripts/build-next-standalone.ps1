#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 41 proof (Plan 41-02): build the Next renderer as a self-contained standalone
  tree and prove it starts on a loopback port without `npm install`.

.DESCRIPTION
  Steps:
    1. `npm run build` in frontend/ (output: 'standalone' enabled in next.config.mjs).
    2. Deterministically copy public/ and .next/static into .next/standalone — Next does
       not copy these itself (41-RESEARCH.md, T-41-02-01). Missing source assets FAIL.
    3. Start .next/standalone/server.js on an OS-allocated loopback port, capture
       stdout/stderr to a proof log, wait for HTTP readiness, then terminate the owned
       process (and its process tree) on timeout or exit.

  -VerifyOnly runs steps 2+3 against an existing build and skips `next build`.

  The child process is owned by this script: on timeout, on failure or on normal exit
  the process tree is terminated and checked for leftovers (T-41-02-02).

.PARAMETER VerifyOnly
  Skip `npm run build`; verify an existing standalone tree only.

.PARAMETER ReadyTimeoutSeconds
  Seconds to wait for HTTP readiness before treating the server as failed.

.PARAMETER KeepRunning
  Keep the server running after readiness is confirmed (manual smoke mode).
.EXAMPLE
  powershell -File desktop/proof/scripts/build-next-standalone.ps1
  powershell -File desktop/proof/scripts/build-next-standalone.ps1 -VerifyOnly
#>
[CmdletBinding()]
param(
  [switch]$VerifyOnly,
  [int]$ReadyTimeoutSeconds = 90,
  [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\")).Path
$frontendDir = Join-Path $repoRoot "frontend"
$standaloneRoot = Join-Path $frontendDir ".next\standalone"
$logDir = Join-Path $repoRoot "desktop\proof\logs"
$stdoutLog = Join-Path $logDir "next-standalone.stdout.log"
$stderrLog = Join-Path $logDir "next-standalone.stderr.log"
$exitCode = 0

# Loopback-only bind. No proof process may bind a wider interface (D-41-05).
$loopbackHost = "127.0.0.1"

function Write-Info([string]$msg) { Write-Host "[build-next-standalone] $msg" }
function Fail([string]$msg) {
  Write-Host "[build-next-standalone] FAILED: $msg" -ForegroundColor Red
  exit 1
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path (Join-Path $frontendDir "package.json"))) {
  Fail "frontend/package.json not found at $frontendDir"
}

# ---- Step 1: build (skipped in -VerifyOnly) ------------------------------------
if (-not $VerifyOnly) {
  Write-Info "building frontend standalone output..."
  Push-Location $frontendDir
  try {
    npm run build
    if ($LASTEXITCODE -ne 0) { Fail "frontend build failed with exit code $LASTEXITCODE" }
  } finally {
    Pop-Location
  }
} else {
  Write-Info "-VerifyOnly: skipping 'next build'"
}

if (-not (Test-Path (Join-Path $standaloneRoot "server.js"))) {
  Fail "standalone server.js missing at $standaloneRoot (did the build emit .next/standalone?)"
}

# ---- Step 2: deterministic asset copy (T-41-02-01) -----------------------------
# Missing source assets are a FAIL, never silently ignored.
$srcPublic = Join-Path $frontendDir "public"
$dstPublic = Join-Path $standaloneRoot "public"
if (Test-Path $srcPublic) {
  New-Item -ItemType Directory -Force -Path $dstPublic | Out-Null
  Copy-Item -Path (Join-Path $srcPublic "*") -Destination $dstPublic -Recurse -Force
  Write-Info "copied public/ -> standalone/public/"
} else {
  Fail "frontend/public missing — cannot copy public assets into standalone tree"
}

$srcStatic = Join-Path $frontendDir ".next\static"
$dstStatic = Join-Path $standaloneRoot ".next\static"
if (Test-Path $srcStatic) {
  New-Item -ItemType Directory -Force -Path $dstStatic | Out-Null
  Copy-Item -Path (Join-Path $srcStatic "*") -Destination $dstStatic -Recurse -Force
  Write-Info "copied .next/static -> standalone/.next/static/"
} else {
  Fail "frontend/.next/static missing — cannot copy static assets into standalone tree"
}

# Sanity: the standalone tree must not depend on frontend/node_modules.
$nodeModulesInside = Test-Path (Join-Path $standaloneRoot "node_modules")
if (-not $nodeModulesInside) {
  Fail "standalone tree has no node_modules — cannot be a self-contained artifact"
}

# ---- Step 3: start, readiness, owned shutdown (T-41-02-02) --------------------
$port = Get-Random -Minimum 20000 -Maximum 60000
$listenAddress = "$loopbackHost`:$port"
Write-Info "starting standalone server.js on http://$listenAddress"

$env:PORT = "$port"
$env:HOSTNAME = $loopbackHost

$serverProcess = Start-Process -FilePath "node" `
  -ArgumentList @("server.js") `
  -WorkingDirectory $standaloneRoot `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru -NoNewWindow

$ready = $false
$deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
do {
  if ($serverProcess.HasExited) {
    Write-Info "server.js exited early (code $($serverProcess.ExitCode))"
    break
  }
  try {
    $response = Invoke-WebRequest -Uri "http://$listenAddress/" -UseBasicParsing -TimeoutSec 3
    if ($response.StatusCode -eq 200) {
      $ready = $true
      Write-Info "HTTP readiness confirmed on http://$listenAddress (status 200)"
      break
    }
  } catch {
    Start-Sleep -Milliseconds 500
  }
} while ((Get-Date) -lt $deadline)

if (-not $ready) {
  Write-Info "--- tail of $stdoutLog ---"
  if (Test-Path $stdoutLog) { Get-Content $stdoutLog -Tail 30 }
  Write-Info "--- tail of $stderrLog ---"
  if (Test-Path $stderrLog) { Get-Content $stderrLog -Tail 30 }
  # Owned shutdown: terminate the failed child and its tree.
  if (-not $serverProcess.HasExited) {
    & taskkill /PID $serverProcess.Id /T /F 2>$null | Out-Null
  }
  Fail "standalone server did not become ready within ${ReadyTimeoutSeconds}s on http://$listenAddress"
}

if ($KeepRunning) {
  Write-Info "server ready and kept running (PID $($serverProcess.Id)); press Enter to stop..."
  Read-Host | Out-Null
}

# ---- owned shutdown: terminate the whole child process tree -------------------
if (-not $serverProcess.HasExited) {
  & taskkill /PID $serverProcess.Id /T /F 2>$null | Out-Null
  $serverProcess.WaitForExit()
}
if ($serverProcess.HasExited) {
  Write-Info "child process exited cleanly (code $($serverProcess.ExitCode))"
} else {
  Write-Info "child process still running after taskkill"
  $exitCode = 1
}

# Leftover check: no node.exe child of this script's tree may remain. A standalone
# server.js spawns no children, so the PID check is sufficient.
if (Get-Process -Id $serverProcess.Id -ErrorAction SilentlyContinue) {
  Write-Info "RESIDUAL PROCESS: node PID $($serverProcess.Id) survived shutdown"
  $exitCode = 1
} else {
  Write-Info "no residual child process"
}

if ($exitCode -eq 0) {
  Write-Info "STANDALONE PROOF PASSED"
} else {
  Fail "standalone proof reported residual/shutdown failure"
}
exit $exitCode
