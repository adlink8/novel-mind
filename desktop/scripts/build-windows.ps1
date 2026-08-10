#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 45 Plan 45-01 Task 2/4: build the deterministic Windows artifact
  (win-unpacked + NSIS) from the hash-pinned staged runtime tree.

.DESCRIPTION
  Orchestration chain:
    1. `npm run build` (tsc emit of the Electron main/preload/renderer shell).
    2. `scripts/stage-runtime.ps1` — stage the Next standalone tree + public +
       .next/static with pinned-hash verification and emit the per-file inventory.
    3. electron-builder --win (config: electron-builder.yml) → dist/win-unpacked
       and dist/NovelMind-Setup-<version>-x64.exe (unsigned local qualification).
    4. Post-build audit: the unpacked app must contain the exe, app.asar and the
       staged next-standalone tree with the exact pinned server.js hash.
    5. Emit dist/CHECKSUMS.SHA256 and dist/bundled-inventory.json.

  -Verify runs the FULL chain twice and compares the two staged inventories
  (file list + per-file hashes) for reproducibility. If electron-builder cannot
  download its NSIS tools/winCodeSign, the unpacked (dir) target still succeeds;
  the failure is reported and the unpacked artifact remains auditable.

  Honest 41 NO-GO boundary: only Electron + embedded Node + Next standalone are
  packaged. Python/PG/vector are NOT bundled (41-DECISION.md PREREQ-2/3/4).

.PARAMETER Verify
  Build twice and compare staged inventories for reproducibility.
.PARAMETER SkipBuilder
  Stage + audit only; skip the electron-builder invocation.
.PARAMETER SkipExeEdit
  Pass --config.win.signAndEditExecutable=false (already the yml default).
.PARAMETER NsisOnly
  Build only the NSIS target (skip the win-unpacked dir target).

.EXAMPLE
  powershell -File desktop/scripts/build-windows.ps1
  powershell -File desktop/scripts/build-windows.ps1 -Verify
#>
[CmdletBinding()]
param(
  [switch]$Verify,
  [switch]$SkipBuilder,
  [switch]$SkipExeEdit,
  [switch]$NsisOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$desktopDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stagedRoot = Join-Path $desktopDir "dist\staged"
$manifest1 = Join-Path $stagedRoot "staged-manifest.json"
$manifest2 = Join-Path $stagedRoot "staged-manifest-verify2.json"
$unpackedDir = Join-Path $desktopDir "dist\win-unpacked"
$distDir = Join-Path $desktopDir "dist"

function Write-Info([string]$msg) { Write-Host "[build-windows] $msg" }
function Fail([string]$msg) {
  Write-Host "[build-windows] FAILED: $msg" -ForegroundColor Red
  exit 1
}
function Get-Sha256([string]$filePath) {
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $filePath).Hash.ToLowerInvariant()
}

# ---- 1. compile the shell ---------------------------------------------------
Write-Info "compiling desktop TypeScript (tsc)..."
Push-Location $desktopDir
try {
  npm run build
  if ($LASTEXITCODE -ne 0) { Fail "desktop build failed with exit code $LASTEXITCODE" }
} finally {
  Pop-Location
}

# ---- 2. stage the runtime tree ----------------------------------------------
$stageScript = Join-Path $PSScriptRoot "stage-runtime.ps1"
Write-Info "staging runtime tree (build 1)..."
& powershell -NoProfile -ExecutionPolicy Bypass -File $stageScript
if ($LASTEXITCODE -ne 0) { Fail "stage-runtime.ps1 failed (exit $LASTEXITCODE)" }

# ---- 3. electron-builder ----------------------------------------------------
if (-not $SkipBuilder) {
  Write-Info "running electron-builder --win (unsigned local qualification)..."
  $builderArgs = @("--win", "--publish", "never")
  if ($SkipExeEdit) { $builderArgs += @("--config.win.signAndEditExecutable=false") }
  if ($NsisOnly) { $builderArgs += @("--config.win.target=nsis") }
  Push-Location $desktopDir
  try {
    & "$desktopDir\node_modules\.bin\electron-builder.cmd" @builderArgs 2>&1 | ForEach-Object { Write-Host "  $($_)" }
    if ($LASTEXITCODE -ne 0) {
      Write-Host "[build-windows] electron-builder exited $LASTEXITCODE — see log above; if this is an NSIS tooling download failure the win-unpacked artifact may still be valid." -ForegroundColor Yellow
    }
  } finally {
    Pop-Location
  }
}

# ---- 4. post-build audit -----------------------------------------------------
function Test-PackagedArtifact {
  param([string]$StagedManifestPath)
  if (-not (Test-Path $unpackedDir)) {
    Write-Host "[build-windows] WARN: $unpackedDir absent — artifact audit skipped (electron-builder may have failed)." -ForegroundColor Yellow
    return $false
  }
  $manifest = Get-Content $StagedManifestPath -Raw | ConvertFrom-Json
  $exe = Join-Path $unpackedDir "NovelMind.exe"
  if (-not (Test-Path $exe)) { $exe = Join-Path $unpackedDir "electron.exe" }
  if (-not (Test-Path $exe)) { Fail "win-unpacked has neither NovelMind.exe nor electron.exe" }
  $asar = Join-Path $unpackedDir "resources\app.asar"
  if (-not (Test-Path $asar)) { Fail "win-unpacked/resources/app.asar missing" }
  $stagedServer = Join-Path $unpackedDir "resources\next-standalone\server.js"
  if (-not (Test-Path $stagedServer)) { Fail "win-unpacked/resources/next-standalone/server.js missing" }
  if (-not (Test-Path (Join-Path $unpackedDir "resources\next-standalone\public"))) { Fail "staged public/ missing in win-unpacked" }
  if (-not (Test-Path (Join-Path $unpackedDir "resources\next-standalone\.next\static"))) { Fail "staged .next/static missing in win-unpacked" }
  # The standalone tree's dependencies must ship too: electron-builder drops a
  # top-level node_modules unless it is a dedicated extraResources matcher
  # (45-03 UAT regression — see 45-03-SUMMARY.md). Without this gate the
  # packaged Next server cannot start.
  if (-not (Test-Path (Join-Path $unpackedDir "resources\next-standalone\node_modules\next"))) {
    Fail "win-unpacked/resources/next-standalone/node_modules/next missing — extraResources dropped the standalone dependencies"
  }
  $actualHash = Get-Sha256 $stagedServer
  if ($actualHash -ne $manifest.pins.serverJsHash) {
    Fail "win-unpacked server.js hash $actualHash != staged manifest $($manifest.pins.serverJsHash)"
  }
  Write-Info "artifact audit PASS: $exe, $asar, resources/next-standalone (server.js $actualHash)"
  return $true
}

$auditOk = Test-PackagedArtifact -StagedManifestPath $manifest1

# ---- 5. checksums + bundled inventory ----------------------------------------
$checksums = @()
$setupExe = Get-ChildItem -Path $distDir -Filter "NovelMind-Setup-*.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($setupExe) {
  $checksums += "$(Get-Sha256 $setupExe.FullName) *$(Split-Path $setupExe.FullName -Leaf)"
}
$unpackedExe = Join-Path $unpackedDir "NovelMind.exe"
if (-not (Test-Path $unpackedExe)) { $unpackedExe = Join-Path $unpackedDir "electron.exe" }
if (Test-Path $unpackedExe) {
  $checksums += "$(Get-Sha256 $unpackedExe) *win-unpacked/$(Split-Path $unpackedExe -Leaf)"
}
$asarFile = Join-Path $unpackedDir "resources\app.asar"
if (Test-Path $asarFile) {
  $checksums += "$(Get-Sha256 $asarFile) *win-unpacked/resources/app.asar"
}
if ($checksums.Count -gt 0) {
  # ASCII, no BOM (see stage-runtime.ps1 for the PowerShell 5.1 BOM note).
  [System.IO.File]::WriteAllText(
    (Join-Path $distDir "CHECKSUMS.SHA256"),
    ($checksums -join "`r`n"),
    [System.Text.Encoding]::ASCII
  )
  Write-Info "wrote dist/CHECKSUMS.SHA256 ($($checksums.Count) entries)"
}

$stagedManifest = Get-Content $manifest1 -Raw | ConvertFrom-Json
$inventory = @{
  schemaVersion = 1
  phase         = "45"
  plan          = "01"
  generatedAt   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  platform      = "win32-x64"
  unsigned      = $true
  serverJsSha256 = $stagedManifest.pins.serverJsHash
  components = @(
    @{ id = "electron"; version = $stagedManifest.pins.electron; role = "shell + embedded Node runtime"; license = "MIT (Electron); embedded Node redistributions carry their own licenses" },
    @{ id = "embedded-node"; version = $stagedManifest.pins.embeddedNode; role = "runtime for next standalone server.js (ELECTRON_RUN_AS_NODE)"; license = "Node.js license (in Electron dist)" },
    @{ id = "next"; version = $stagedManifest.pins.next; role = "standalone renderer (server.js)"; license = "MIT" },
    @{ id = "react"; version = $stagedManifest.pins.react; role = "renderer library"; license = "MIT" }
  )
  notBundledBoundary = @{
    note = "41-DECISION.md NO-GO: only proven runtimes are packaged. Python/FastAPI, PostgreSQL/pgvector and the vector store are NOT bundled and remain prerequisites for plans after 45."
    components = @("fastapi", "agent_service", "postgres_pgvector", "vector_store")
  }
}
# UTF-8 WITHOUT BOM (see stage-runtime.ps1 for the PowerShell 5.1 BOM note).
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
  (Join-Path $distDir "bundled-inventory.json"),
  ($inventory | ConvertTo-Json -Depth 4),
  $utf8NoBom
)
Write-Info "wrote dist/bundled-inventory.json"

# ---- -Verify: build twice + compare staged inventories -----------------------
if ($Verify) {
  Write-Info "Verify mode: staging runtime tree a second time..."
  & powershell -NoProfile -ExecutionPolicy Bypass -File $stageScript -OutManifest $manifest2
  if ($LASTEXITCODE -ne 0) { Fail "second stage-runtime.ps1 failed (exit $LASTEXITCODE)" }

  $a = Get-Content $manifest1 -Raw | ConvertFrom-Json
  $b = Get-Content $manifest2 -Raw | ConvertFrom-Json
  $diffs = @()
  if ($a.fileCount -ne $b.fileCount) { $diffs += "file count: $($a.fileCount) vs $($b.fileCount)" }
  if ($a.pins.serverJsHash -ne $b.pins.serverJsHash) { $diffs += "serverJsHash differs" }
  $bByPath = @{}
  foreach ($entry in $b.files) { $bByPath[$entry.path] = $entry }
  foreach ($entry in $a.files) {
    $other = $bByPath[$entry.path]
    if (-not $other) { $diffs += "build 2 missing: $($entry.path)"; continue }
    if ($other.sha256 -ne $entry.sha256) { $diffs += "hash differs: $($entry.path)" }
  }
  if ($diffs.Count -gt 0) {
    foreach ($d in $diffs) { Write-Host "  DIFF: $d" -ForegroundColor Red }
    Fail "staged inventories are NOT reproducible across two builds ($($diffs.Count) diffs)"
  }
  Write-Info "reproducibility PASS: two staged inventories match ($($a.fileCount) files)"

  if (-not $SkipBuilder) {
    Write-Info "Verify mode: second electron-builder pass..."
    Push-Location $desktopDir
    try {
      $builderArgs2 = @("--win", "--publish", "never")
      if ($SkipExeEdit) { $builderArgs2 += @("--config.win.signAndEditExecutable=false") }
      & "$desktopDir\node_modules\.bin\electron-builder.cmd" @builderArgs2 2>&1 | ForEach-Object { Write-Host "  $($_)" }
      if ($LASTEXITCODE -ne 0) {
        Write-Host "[build-windows] second electron-builder exited $LASTEXITCODE." -ForegroundColor Yellow
      }
    } finally {
      Pop-Location
    }
    $auditOk = Test-PackagedArtifact -StagedManifestPath $manifest1
  }
}

if (-not $auditOk) {
  Write-Host "[build-windows] DONE with warnings: win-unpacked audit was skipped because the artifact is absent (see electron-builder output)." -ForegroundColor Yellow
  exit 2
}
Write-Info "BUILD PASSED"
