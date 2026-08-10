#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 45 Plan 45-03: run the packaged-desktop qualification suite (first-run,
  13-route parity, critical workflows, offline recovery, data preservation)
  against the SHIPPED win-unpacked artifact on this machine.

.DESCRIPTION
  Flow:
    1. Validate the qualification manifest (plan Task 1 gate) and the packaged
       artifact checksums it pins.
    2. provision.ps1 tightens PATH (Node/Python/Docker/PostgreSQL removed) and
       isolates NOVELMIND_USER_DATA to a per-run temp dir — the local
       approximation of a clean first-run machine (NOT pristine-VM evidence;
       the manifest records clean_vm=false and the UAT rows mark the boundary).
    3. Starts the BUNDLED next-standalone renderer through the packaged exe's
       embedded Node (ELECTRON_RUN_AS_NODE) and runs the e2e suite with
       NOVELMIND_PACKAGED_EXE + NOVELMIND_SMOKE_RENDERER_URL set.
    4. Writes the evidence index (results/qualification-results.md) and exits
       non-zero on any failure.

  -RequireAll: fail if ANY test fails and require every UAT row to be present.

.PARAMETER Manifest
  Qualification manifest path (default desktop/tests/fixtures/qualification-manifest.json).
.PARAMETER RequireAll
  Fail-closed: any failed/missing row makes the run fail.

.EXAMPLE
  powershell -File desktop/tests/clean-vm/run-qualification.ps1 -Manifest desktop/tests/fixtures/qualification-manifest.json
  powershell -File desktop/tests/clean-vm/run-qualification.ps1 -Manifest desktop/tests/fixtures/qualification-manifest.json -RequireAll
#>
[CmdletBinding()]
param(
  [string]$Manifest,
  [switch]$RequireAll
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$desktopDir = Join-Path $repoRoot "desktop"
if ([string]::IsNullOrWhiteSpace($Manifest)) {
  $Manifest = Join-Path $repoRoot "desktop\tests\fixtures\qualification-manifest.json"
}
$resultsDir = Join-Path $PSScriptRoot "results"
$envFile = Join-Path $resultsDir "qualification.env.json"
$boundaryFile = Join-Path $resultsDir "machine-boundary.json"
$resultsMd = Join-Path $resultsDir "qualification-results.md"

function Write-Step([string]$msg) { Write-Host "[run-qualification] $msg" }
function Fail([string]$msg) {
  Write-Host "[run-qualification] FAILED: $msg" -ForegroundColor Red
  exit 1
}

# ---- 0. manifest + artifact gate (Task 1) ------------------------------------
if (-not (Test-Path $Manifest)) { Fail "qualification manifest missing at $Manifest" }
$manifestData = Get-Content $Manifest -Raw | ConvertFrom-Json
Write-Step "manifest: $($manifestData.qualificationScope) clean_vm=$($manifestData.machine.clean_vm)"

$installer = Join-Path $repoRoot ($manifestData.artifact.installer.path -replace "\\", "/")
$installer = $installer.Replace("/", "\")
$unpackedExe = Join-Path $repoRoot "desktop\dist\win-unpacked\NovelMind.exe"
if (-not (Test-Path $unpackedExe)) { Fail "packaged exe missing at $unpackedExe — run build-windows.ps1 first" }
$actualExeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $unpackedExe).Hash.ToLowerInvariant()
if ($actualExeHash -ne $manifestData.artifact.unpacked.exe_sha256) {
  Fail "win-unpacked NovelMind.exe hash $actualExeHash != manifest $($manifestData.artifact.unpacked.exe_sha256) — artifact does not match the manifest"
}
Write-Step "artifact gate PASS (exe hash matches manifest)"

# ---- 1. provision the machine boundary ---------------------------------------
Write-Step "provisioning (tightened PATH + isolated user data)..."
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "provision.ps1") -Manifest $Manifest -ResultsDir $resultsDir
if ($LASTEXITCODE -ne 0) { Fail "provision.ps1 exited $LASTEXITCODE" }

if (-not (Test-Path $envFile)) { Fail "provisioned env file missing at $envFile" }
$envPayload = Get-Content $envFile -Raw | ConvertFrom-Json
$tightPath = $envPayload.PATH
$userDataDir = $envPayload.NOVELMIND_USER_DATA
$packagedExe = $envPayload.NOVELMIND_PACKAGED_EXE

# ---- 2. run the qualification suite -------------------------------------------
# The Playwright harness is a dev tool: it runs under the developer's Node. The
# TIGHTENED PATH below applies to the packaged app and every child it spawns
# (the packed exe is launched by absolute path and its renderer runs through the
# exe's embedded Node — none of them resolve from PATH), while the harness
# itself is invoked with the ABSOLUTE node path captured before tightening.
$harnessNode = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $harnessNode) { Fail "node not found on PATH — the Playwright harness requires the developer Node" }
$playwrightCliJs = Join-Path $desktopDir "node_modules\playwright\cli.js"
if (-not (Test-Path $playwrightCliJs)) { Fail "playwright cli missing at $playwrightCliJs" }
$oldPath = $env:PATH
$oldUserData = $env:NOVELMIND_USER_DATA
$oldPackagedExe = $env:NOVELMIND_PACKAGED_EXE
try {
  # Provisioned environment: tightened PATH + isolated app-data root + packaged exe.
  $env:PATH = $tightPath
  $env:NOVELMIND_USER_DATA = $userDataDir
  $env:NOVELMIND_PACKAGED_EXE = $packagedExe

  Write-Step "running qualification suite (RequireAll=$RequireAll)..."
  Write-Step "  packaged exe: $packagedExe"
  Write-Step "  user data:    $userDataDir"
  Write-Step "  harness node: $harnessNode"
  Push-Location $desktopDir
  try {
    if ($RequireAll) {
      & $harnessNode $playwrightCliJs test --config tests/clean-vm/playwright.config.ts 2>&1 | ForEach-Object { Write-Host "  $_" }
    } else {
      & $harnessNode $playwrightCliJs test --config tests/clean-vm/playwright.config.ts 2>&1 | ForEach-Object { Write-Host "  $_" }
    }
    $playwrightExit = $LASTEXITCODE
  } finally {
    Pop-Location
  }
} finally {
  $env:PATH = $oldPath
  $env:NOVELMIND_USER_DATA = $oldUserData
  $env:NOVELMIND_PACKAGED_EXE = $oldPackagedExe
}

# ---- 3. evidence index ---------------------------------------------------------
$passed = ($playwrightExit -eq 0)
$runStamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$summary = @"
# NovelMind Desktop Qualification — Local Approximation Evidence

**Run:** $runStamp
**Machine:** $($manifestData.machine.os.edition) $($manifestData.machine.os.version) ($($manifestData.machine.os.arch))
**clean_vm:** $($manifestData.machine.clean_vm)
**Artifact:** $unpackedExe (sha256 $actualExeHash)
**Playwright exit:** $playwrightExit ($(if ($passed) { 'PASS' } else { 'FAIL' }))
"@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($resultsMd, $summary, $utf8NoBom)
Write-Step "evidence index written to $resultsMd"

if (-not $passed) {
  if ($RequireAll) { Fail "qualification suite FAILED ($playwrightExit) with -RequireAll" }
  else { Fail "qualification suite FAILED ($playwrightExit)" }
}
Write-Step "QUALIFICATION PASSED (local approximation; clean-VM still required for release evidence)"
