#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 45 Plan 45-01 Task 2: stage the hash-pinned Next standalone renderer tree
  (server.js + node_modules + package.json + public/ + .next/static/) into the
  packaging resource tree and emit a per-file SHA-256 inventory manifest.

.DESCRIPTION
  Only the Phase 41-proven runtimes are staged (T-45-01-01):
    - Electron 43.3.0 + embedded Node v24.18.1 (used at runtime as the Node binary)
    - The Next standalone tree (frontend/.next/standalone + frontend/public +
      frontend/.next/static)

  The source server.js hash MUST match the pinned hash in
  desktop/proof/runtime-manifest.json (the exact artifact the route-parity
  proof exercised); a mismatch fails closed. The staged tree is emitted to
  <desktop>/dist/staged/next-standalone and the inventory to
  <desktop>/dist/staged/staged-manifest.json.

  -VerifyOnly recomputes hashes of an existing staged tree and compares them to
  the existing manifest (no copy, no source hashing) — used for the build-twice
  reproducibility check.

  Honest boundary: bundled Python/FastAPI, PostgreSQL/pgvector and the vector
  store are NOT staged here (41-DECISION.md NO-GO, PREREQ-2/3/4). See
  docs/desktop-installation.md.

.PARAMETER Manifest
  Path to the Phase 41 runtime manifest that pins the server.js hash and Electron
  version. Default: desktop/proof/runtime-manifest.json.

.PARAMETER OutManifest
  Output inventory path. Default: desktop/dist/staged/staged-manifest.json.

.PARAMETER VerifyOnly
  Recompute the staged tree hashes and compare to the existing OutManifest.

.EXAMPLE
  powershell -File desktop/scripts/stage-runtime.ps1
  powershell -File desktop/scripts/stage-runtime.ps1 -VerifyOnly
#>
[CmdletBinding()]
param(
  [string]$Manifest,
  [string]$OutManifest,
  [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$desktopDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $desktopDir "..")).Path
$frontendDir = Join-Path $repoRoot "frontend"
$standaloneRoot = Join-Path $frontendDir ".next\standalone"
$stagedRoot = Join-Path $desktopDir "dist\staged"
$stagedTree = Join-Path $stagedRoot "next-standalone"

if (-not $Manifest) { $Manifest = Join-Path $desktopDir "proof\runtime-manifest.json" }
if (-not $OutManifest) { $OutManifest = Join-Path $stagedRoot "staged-manifest.json" }

function Write-Info([string]$msg) { Write-Host "[stage-runtime] $msg" }
function Fail([string]$msg) {
  Write-Host "[stage-runtime] FAILED: $msg" -ForegroundColor Red
  exit 1
}

function Get-Sha256([string]$filePath) {
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $filePath).Hash.ToLowerInvariant()
}

# ---- source / pin validation ------------------------------------------------
if (-not (Test-Path $Manifest)) { Fail "runtime manifest not found at $Manifest" }
$pinnedManifest = Get-Content $Manifest -Raw | ConvertFrom-Json

$serverJs = Join-Path $standaloneRoot "server.js"
if (-not (Test-Path $serverJs)) { Fail "standalone server.js missing at $serverJs (build the frontend first)" }

$pinnedServerHash = $pinnedManifest.components[0].runtimeArtifact.hash
$pinnedElectron = $pinnedManifest.environment.electron.packageVersion
$pinnedEmbeddedNode = $pinnedManifest.environment.electron.embeddedNodeDeclared

$actualServerHash = Get-Sha256 $serverJs
Write-Info "server.js sha256: $actualServerHash"
if ($actualServerHash -ne $pinnedServerHash) {
  Fail "server.js hash does not match the pinned proof hash ($pinnedServerHash) — refusing to stage an unproven artifact (T-45-01-01)"
}

$electronPkg = Join-Path $desktopDir "node_modules\electron\package.json"
if (-not (Test-Path $electronPkg)) { Fail "electron package.json missing at $electronPkg" }
$electronVersion = (Get-Content $electronPkg -Raw | ConvertFrom-Json).version
if ($electronVersion -ne $pinnedElectron) {
  Fail "electron version $electronVersion does not match pinned $pinnedElectron"
}

# ---- VerifyOnly: hash audit of an existing staged tree ----------------------
if ($VerifyOnly) {
  if (-not (Test-Path $OutManifest)) { Fail "-VerifyOnly requested but no staged manifest at $OutManifest" }
  if (-not (Test-Path $stagedTree)) { Fail "-VerifyOnly requested but no staged tree at $stagedTree" }
  $prior = Get-Content $OutManifest -Raw | ConvertFrom-Json

  $files = Get-ChildItem -Path $stagedTree -Recurse -File
  $fileCount = $files.Count
  $mismatches = @()
  foreach ($f in $files) {
    $rel = $f.FullName.Substring($stagedTree.Length + 1).Replace("\", "/")
    $hash = Get-Sha256 $f.FullName
    $priorEntry = $prior.files | Where-Object { $_.path -eq $rel } | Select-Object -First 1
    if (-not $priorEntry) {
      $mismatches += "unlisted staged file: $rel"
    } elseif ($priorEntry.sha256 -ne $hash) {
      $mismatches += "hash mismatch: $rel ($priorEntry.sha256 vs $hash)"
    }
  }
  if ($prior.fileCount -ne $fileCount) {
    $mismatches += "file count drift: manifest $($prior.fileCount) vs staged $fileCount"
  }
  if ($mismatches.Count -gt 0) {
    foreach ($m in $mismatches) { Write-Host "  MISMATCH: $m" -ForegroundColor Red }
    Fail "staged tree does not reproduce the declared inventory ($($mismatches.Count) discrepancies)"
  }
  Write-Info "VerifyOnly PASS: $fileCount files reproduce the declared inventory"
  exit 0
}

# ---- stage the tree ----------------------------------------------------------
if (Test-Path $stagedTree) { Remove-Item -Recurse -Force $stagedTree }
New-Item -ItemType Directory -Force -Path $stagedTree | Out-Null

# 1. Bulk copy the standalone tree (server.js + node_modules + package.json).
#    `*.map` (Next server source maps) are excluded: they are debugging-only,
#    leak server source and never run in production (T-45-01-01).
& robocopy $standaloneRoot $stagedTree /E /NFL /NDL /NJH /NJS /NP /XF *.map | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy of the standalone tree failed (exit $LASTEXITCODE)" }

# 2. Deterministic asset copy: public/ and .next/static are REQUIRED (a missing
#    source is a FAIL, never silently ignored — mirrors 41-02).
$srcPublic = Join-Path $frontendDir "public"
if (Test-Path $srcPublic) {
  New-Item -ItemType Directory -Force -Path (Join-Path $stagedTree "public") | Out-Null
  & robocopy $srcPublic (Join-Path $stagedTree "public") /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { Fail "robocopy of public/ failed (exit $LASTEXITCODE)" }
} else {
  Fail "frontend/public missing — cannot stage public assets"
}

$srcStatic = Join-Path $frontendDir ".next\static"
$dstStaticDir = Join-Path $stagedTree ".next\static"
if (Test-Path $srcStatic) {
  New-Item -ItemType Directory -Force -Path $dstStaticDir | Out-Null
  & robocopy $srcStatic $dstStaticDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { Fail "robocopy of .next/static failed (exit $LASTEXITCODE)" }
} else {
  Fail "frontend/.next/static missing — cannot stage static assets"
}

# The staged tree must be self-contained (no first-run npm/download prerequisite).
if (-not (Test-Path (Join-Path $stagedTree "node_modules"))) {
  Fail "staged tree has no node_modules — not self-contained"
}

# ---- inventory ---------------------------------------------------------------
$stagedPkg = Get-Content (Join-Path $stagedTree "package.json") -Raw | ConvertFrom-Json
$allFiles = Get-ChildItem -Path $stagedTree -Recurse -File
$inventory = New-Object System.Collections.ArrayList
$totalBytes = 0L
foreach ($f in $allFiles) {
  $rel = $f.FullName.Substring($stagedTree.Length + 1).Replace("\", "/")
  $hash = Get-Sha256 $f.FullName
  [void]$inventory.Add(@{ path = $rel; sha256 = $hash; size = $f.Length })
  $totalBytes += $f.Length
}
$sortedInventory = @($inventory | Sort-Object { $_.path })

# NOTE: never name this `$manifest` — `$Manifest` is a [string]-typed parameter and
# PowerShell variable names are case-insensitive, so a hashtable assigned to it
# would be coerced to its type name ("System.Collections.Hashtable").
$inventoryManifest = @{
  schemaVersion = 1
  phase         = "45"
  plan          = "01"
  source        = "next-standalone"
  generatedAt   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  pins          = @{
    electron        = $pinnedElectron
    embeddedNode    = $pinnedEmbeddedNode
    next            = $stagedPkg.dependencies.next
    react           = $stagedPkg.dependencies.react
    serverJsHash    = $actualServerHash
    pinnedServerHash = $pinnedServerHash
  }
  fileCount     = $allFiles.Count
  totalBytes    = $totalBytes
  files         = $sortedInventory
}
$manifestJson = $inventoryManifest | ConvertTo-Json -Depth 4
# UTF-8 WITHOUT BOM — Set-Content -Encoding utf8 writes a BOM in Windows PowerShell
# 5.1, which breaks strict JSON parsers (JSON.parse in the audit suite).
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($OutManifest, $manifestJson, $utf8NoBom)

# Artifact checksum evidence file.
$checksumLines = @()
foreach ($entry in $sortedInventory) {
  $checksumLines += "$($entry.sha256) *$($entry.path)"
}
[System.IO.File]::WriteAllText(
  (Join-Path $stagedRoot "CHECKSUMS.SHA256"),
  ($checksumLines -join "`r`n"),
  [System.Text.Encoding]::ASCII
)

Write-Info "staged $($allFiles.Count) files ($([math]::Round($totalBytes / 1MB, 1)) MB) to $stagedTree"
Write-Info "server.js $actualServerHash (pinned $pinnedServerHash)"
Write-Info "inventory written to $OutManifest"
Write-Info "STAGE PASSED"
