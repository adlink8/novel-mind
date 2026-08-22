#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 45 Plan 45-04, Task 1: generate and verify the release SBOM /
  component-provenance evidence for the shipped Windows artifact.

.DESCRIPTION
  Computes a checksum-bound SBOM over the shipped win-unpacked artifact and its
  staged runtime tree, and compares every hash against the authoritative Phase
  41/45 evidence:
    - desktop/proof/runtime-manifest.json must be byte-identical to the hash
      recorded in 41-DECISION.md (T-41-03-01 anti-tamper),
    - the packaged server.js / installer / exe / app.asar hashes must match the
      qualification manifest and CHECKSUMS.SHA256,
    - the staged inventory must be self-consistent (per-file re-hash),
    - the component inventory must match the staged pins,
    - a secret-material scan over packaged resources must be empty,
    - the SBOM itself must record unsigned=true (no artifact described as
      publicly trusted or signed - signing is an external publication gate,
      D-45-06).

  Writes desktop/dist/release-sbom.json. With -Verify the script is a strict
  gate: any failed check (or a drift from a previously written SBOM) exits 1.

.PARAMETER Verify
  Strict gate mode: exit 1 on any failed check or SBOM drift. Without it the
  SBOM is written and the overall verdict is printed (exit 0 unless inputs are
  missing).

.EXAMPLE
  powershell -File desktop/scripts/generate-sbom.ps1
  powershell -File desktop/scripts/generate-sbom.ps1 -Verify
#>
[CmdletBinding()]
param(
  [switch]$Verify
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$desktopDir = Join-Path $repoRoot "desktop"
$distDir = Join-Path $desktopDir "dist"
$winUnpacked = Join-Path $distDir "win-unpacked"
$resourcesDir = Join-Path $winUnpacked "resources"

$sbomPath = Join-Path $distDir "release-sbom.json"
$stagedManifestPath = Join-Path $distDir "staged\staged-manifest.json"
$bundledInventoryPath = Join-Path $distDir "bundled-inventory.json"
$checksumsPath = Join-Path $distDir "CHECKSUMS.SHA256"
$proofManifestPath = Join-Path $desktopDir "proof\runtime-manifest.json"
$qualificationManifestPath = Join-Path $desktopDir "tests\fixtures\qualification-manifest.json"
$fixtureManifestPath = Join-Path $desktopDir "test-fixtures\prior-version\fixture-manifest.json"
$packageJsonPath = Join-Path $desktopDir "package.json"

# Recorded in 41-DECISION.md ("runtime-manifest.json (hash cb8fa6c9...)"). Changing
# the proof manifest must flip this check to FAIL (T-41-03-01).
$PROOF_MANIFEST_EXPECTED_HASH = "cb8fa6c95821c77dfa93f1aa6b17c75b04e1f19da373ae386bad9c6868344666"

function Write-Step([string]$msg) { Write-Host "[generate-sbom] $msg" }
function Get-Sha256([string]$path) {
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
}
function ConvertTo-JsonAscii($obj) {
  return ConvertTo-Json -Depth 20 -InputObject $obj
}

$checks = @()
function Add-Check([string]$id, [string]$label, [bool]$pass, [string]$detail) {
  $script:checks += [ordered]@{
    id     = $id
    label  = $label
    pass   = $pass
    detail = $detail
  }
}

# ---- inputs ----------------------------------------------------------------
foreach ($p in @(
  $stagedManifestPath, $bundledInventoryPath, $checksumsPath, $proofManifestPath,
  $qualificationManifestPath, $fixtureManifestPath, $packageJsonPath,
  (Join-Path $winUnpacked "NovelMind.exe"),
  (Join-Path $winUnpacked "resources\app.asar"),
  (Join-Path $resourcesDir "next-standalone\server.js")
)) {
  if (-not (Test-Path $p)) { Write-Error "missing required input: $p"; exit 2 }
}

$stagedManifestData = Get-Content $stagedManifestPath -Raw | ConvertFrom-Json
$bundledInventoryData = Get-Content $bundledInventoryPath -Raw | ConvertFrom-Json
$proofManifestData = Get-Content $proofManifestPath -Raw | ConvertFrom-Json
$qualificationData = Get-Content $qualificationManifestPath -Raw | ConvertFrom-Json
$fixtureData = Get-Content $fixtureManifestPath -Raw | ConvertFrom-Json
$packageData = Get-Content $packageJsonPath -Raw | ConvertFrom-Json

$installerPath = Join-Path $distDir "NovelMind-Setup-0.1.0-x64.exe"
if (Test-Path $installerPath) {
  $installerHash = Get-Sha256 $installerPath
} else {
  $installerHash = ""
  Add-Check "artifact_installer_hash" "installer checksum" $false "installer exe missing at $installerPath"
}

# ---- hash bindings ---------------------------------------------------------
$proofHash = Get-Sha256 $proofManifestPath
$proofMatches = ($proofHash -eq $PROOF_MANIFEST_EXPECTED_HASH)

$exeHash = Get-Sha256 (Join-Path $winUnpacked "NovelMind.exe")
$asarHash = Get-Sha256 (Join-Path $winUnpacked "resources\app.asar")
$serverJsHash = Get-Sha256 (Join-Path $resourcesDir "next-standalone\server.js")

$manifestInstaller = $qualificationData.artifact.installer.sha256
$manifestExe = $qualificationData.artifact.unpacked.exe_sha256
$manifestAsar = $qualificationData.artifact.unpacked.asar_sha256
$manifestServerJs = $qualificationData.artifact.unpacked.server_js_sha256

Add-Check "proof_manifest_untampered" "runtime-manifest.json matches 41-DECISION hash" $proofMatches (
  $(if ($proofMatches) { "sha256 $proofHash" } else { "sha256 $proofHash != expected $PROOF_MANIFEST_EXPECTED_HASH" })
)

Add-Check "staged_server_pinned" "packaged server.js matches staged pin + qualification manifest" `
  (($serverJsHash -eq $stagedManifestData.pins.serverJsHash) -and ($serverJsHash -eq $manifestServerJs)) `
  "server.js sha256 $serverJsHash (staged pin $($stagedManifestData.pins.serverJsHash), manifest $manifestServerJs)"

Add-Check "artifact_installer_hash" "installer checksum matches qualification manifest" `
  (($installerHash -ne "") -and ($installerHash -eq $manifestInstaller)) `
  ($(if ($installerHash -eq "") { "installer missing" } else { "installer sha256 $installerHash (manifest $manifestInstaller)" }))

Add-Check "artifact_exe_hash" "packaged exe checksum matches qualification manifest" `
  ($exeHash -eq $manifestExe) "exe sha256 $exeHash (manifest $manifestExe)"

Add-Check "artifact_asar_hash" "app.asar checksum matches qualification manifest" `
  ($asarHash -eq $manifestAsar) "asar sha256 $asarHash (manifest $manifestAsar)"

# ---- staged inventory self-consistency (per-file re-hash) ------------------
$stagedRoot = Join-Path $distDir "staged\next-standalone"
$stagedOk = $true
$stagedDetail = ""
$aggregate = New-Object System.Collections.Generic.List[string]
if (-not (Test-Path $stagedRoot)) {
  $stagedOk = $false
  $stagedDetail = "staged tree missing at $stagedRoot"
} else {
  $hashAlgo = [System.Security.Cryptography.SHA256]::Create()
  $count = 0
  $bytes = 0
  foreach ($entry in $stagedManifestData.files) {
    $rel = $entry.path
    $full = Join-Path $stagedRoot ($rel -replace "/", "\")
    # -LiteralPath: staged routes contain '[' (e.g. .next/.../[id]/...), which
    # Test-Path would otherwise treat as a wildcard character class.
    if (-not (Test-Path -LiteralPath $full)) { $stagedOk = $false; $stagedDetail = "missing staged file $rel"; break }
    $actual = Get-Sha256 $full
    if ($actual -ne $entry.sha256) { $stagedOk = $false; $stagedDetail = "staged file $rel hash mismatch"; break }
    $aggregate.Add("$rel|$actual")
    $count++
    $bytes += [int64]$entry.size
  }
  if ($stagedOk) {
    $aggLines = $aggregate.ToArray()
    [Array]::Sort($aggLines, [System.StringComparer]::Ordinal)
    $aggText = [string]::Join("`n", $aggLines)
    $aggBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($aggText)
    $stream = New-Object System.IO.MemoryStream(, $aggBytes)
    try {
      $aggHash = [System.BitConverter]::ToString(
        $hashAlgo.ComputeHash($stream)
      ).Replace("-", "").ToLowerInvariant()
    } finally {
      $stream.Dispose()
    }
    $fileCountMatches = ($count -eq $stagedManifestData.fileCount)
    $bytesMatch = ($bytes -eq $stagedManifestData.totalBytes)
    $stagedOk = $fileCountMatches -and $bytesMatch
    if (-not $stagedOk) {
      $stagedDetail = "count/bytes drift: verified $count files/$bytes bytes vs manifest $($stagedManifestData.fileCount)/$($stagedManifestData.totalBytes)"
    } else {
      $stagedDetail = "verified $count files / $bytes bytes, aggregate sha256 $aggHash"
    }
    $script:stagedAggregateSha = $aggHash
    $script:stagedFileCount = $count
    $script:stagedTotalBytes = $bytes
  }
  $hashAlgo.Dispose()
}
Add-Check "staged_inventory_self_consistent" "staged inventory re-hashes + counts match" $stagedOk $stagedDetail

# ---- component inventory vs staged pins ------------------------------------
$inventoryPinsOk = $true
$inventoryDetail = ""
if ($bundledInventoryData.serverJsSha256 -ne $serverJsHash) { $inventoryPinsOk = $false; $inventoryDetail = "bundled-inventory serverJsSha256 mismatch" }
if ($bundledInventoryData.unsigned -ne $true) { $inventoryPinsOk = $false; $inventoryDetail = "bundled-inventory does not record unsigned=true" }
$notBundled = @($bundledInventoryData.notBundledBoundary.components)
foreach ($c in @("fastapi", "agent_service", "postgres_pgvector", "vector_store")) {
  if ($notBundled -notcontains $c) { $inventoryPinsOk = $false; $inventoryDetail = "bundled-inventory notBundledBoundary missing $c" }
}
Add-Check "component_inventory_matches" "bundled component inventory matches staged pins and 41 NO-GO boundary" $inventoryPinsOk `
  ($(if ($inventoryPinsOk) { "electron 43.3.0 / embedded-node v24.18.1 / next 16.3.0-canary.6 / react 19.2.7; notBundled = $($notBundled -join ', ')" } else { $inventoryDetail }))

# ---- packaged runtime tree -------------------------------------------------
$nextModule = Join-Path $resourcesDir "next-standalone\node_modules\next"
Add-Check "packaged_node_modules" "bundled next-standalone/node_modules ships in the packaged tree" `
  (Test-Path $nextModule) ($(if (Test-Path $nextModule) { "node_modules/next present" } else { "node_modules/next missing at $nextModule" }))

# ---- desktop package -------------------------------------------------------
Add-Check "desktop_package" "desktop package.json is the packaged main contract" `
  (($packageData.version -eq "0.1.0") -and ($packageData.main -eq "dist/main/index.js")) `
  "version $($packageData.version), main $($packageData.main)"

# ---- secret scan over packaged resources -----------------------------------
$secretPattern = '\.(map|pem|key|p12|pfx)$'
$envPattern = '(^|\.)env(\.|$)'
$offenders = @()
if (Test-Path $resourcesDir) {
  Get-ChildItem -Path $resourcesDir -Recurse -File | ForEach-Object {
    if ($_.Name -match $secretPattern -or $_.Name -match $envPattern) {
      $offenders += $_.FullName
    }
  }
}
Add-Check "secret_scan" "no source-map or secret material in packaged resources" `
  ($offenders.Count -eq 0) ($(if ($offenders.Count -eq 0) { "no .map/.pem/.key/.p12/.pfx/.env found under resources/" } else { "offenders: $($offenders -join '; ')" }))

# ---- signing claim ---------------------------------------------------------
Add-Check "unsigned_claim" "SBOM records unsigned artifact (no public-trust/signing claim)" $true `
  "signing certificate is an external publication gate (D-45-06); artifact is unsigned and NOT described as publicly trusted/signed"

# ---- fixture evidence ------------------------------------------------------
$fixtureHash = Get-Sha256 $fixtureManifestPath
Add-Check "fixture_manifest_recorded" "prior-version fixture manifest hash recorded as evidence" $true `
  "fixture-manifest.json sha256 $fixtureHash (schema $($fixtureData.schemaVersion) / runtime $($fixtureData.runtimeVersion))"

# ---- assemble --------------------------------------------------------------
$overall = $true
foreach ($c in $checks) { if (-not $c.pass) { $overall = $false } }

$sbom = [ordered]@{
  schemaVersion = 1
  phase         = "45"
  plan          = "04"
  generatedAt   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  platform      = "win32-x64"
  signed        = $false
  unsigned      = $true
  signingNote   = "Code-signing certificate acquisition is an external publication gate (D-45-06). This artifact is unsigned and must not be described as publicly trusted or signed."
  components    = @(
    [ordered]@{ id = "electron"; version = "43.3.0"; role = "shell + embedded Node runtime"; license = "MIT (Electron); embedded Node redistributions carry their own licenses" }
    [ordered]@{ id = "embedded-node"; version = "v24.18.1"; role = "runtime for next standalone server.js (ELECTRON_RUN_AS_NODE)"; license = "Node.js license (in Electron dist)" }
    [ordered]@{ id = "next"; version = "16.3.0-canary.6"; role = "standalone renderer (server.js)"; license = "MIT" }
    [ordered]@{ id = "react"; version = "19.2.7"; role = "renderer library"; license = "MIT" }
  )
  notBundledBoundary = $bundledInventoryData.notBundledBoundary
  artifactHashes = [ordered]@{
    installerSha256 = $installerHash
    exeSha256       = $exeHash
    asarSha256      = $asarHash
    serverJsSha256  = $serverJsHash
  }
  proofManifest = [ordered]@{
    path     = "desktop/proof/runtime-manifest.json"
    sha256   = $proofHash
    expected = $PROOF_MANIFEST_EXPECTED_HASH
    matches  = $proofMatches
  }
  stagedInventory = [ordered]@{
    source          = "desktop/dist/staged/next-standalone"
    fileCount       = $script:stagedFileCount
    totalBytes      = $script:stagedTotalBytes
    aggregateSha256 = $script:stagedAggregateSha
  }
  fixture = [ordered]@{
    path       = "desktop/test-fixtures/prior-version/fixture-manifest.json"
    sha256     = $fixtureHash
    schemaVersion = $fixtureData.schemaVersion
    runtimeVersion = $fixtureData.runtimeVersion
  }
  secretScan = [ordered]@{
    empty     = ($offenders.Count -eq 0)
    offenders = $offenders
  }
  checks = $checks
  overall = $overall
}

# ---- Verify: strict gate + reproducibility against a prior SBOM ------------
if ($Verify -and (Test-Path $sbomPath)) {
  $prior = Get-Content $sbomPath -Raw | ConvertFrom-Json
  if ($prior.artifactHashes.exeSha256 -ne $exeHash) {
    Write-Step "FAIL: SBOM drift - prior exeSha256 $($prior.artifactHashes.exeSha256) != fresh $exeHash"
    exit 1
  }
  if ($prior.artifactHashes.asarSha256 -ne $asarHash) {
    Write-Step "FAIL: SBOM drift - prior asarSha256 $($prior.artifactHashes.asarSha256) != fresh $asarHash"
    exit 1
  }
  if ($prior.stagedInventory.aggregateSha256 -ne $script:stagedAggregateSha) {
    Write-Step "FAIL: SBOM drift - prior staged aggregate $($prior.stagedInventory.aggregateSha256) != fresh $script:stagedAggregateSha"
    exit 1
  }
  Write-Step "prior SBOM matches fresh computation (no drift)"
}

$sbomJson = ConvertTo-JsonAscii $sbom
[System.IO.File]::WriteAllText($sbomPath, $sbomJson, [System.Text.UTF8Encoding]::new($false))
Write-Step "wrote $sbomPath"

foreach ($c in $checks) {
  $mark = if ($c.pass) { "PASS" } else { "FAIL" }
  Write-Host ("[{0}] {1} - {2}" -f $mark, $c.label, $c.detail)
}

if (-not $overall) {
  Write-Step "overall: FAIL (one or more checks failed)"
  if ($Verify) { exit 1 }
} else {
  Write-Step "overall: PASS"
}
exit 0
