#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 41 proof (Plan 41-03): verify bundled-runtime feasibility from the proof layout
  with Docker unavailable and system Node/Python/PostgreSQL removed from PATH.

.DESCRIPTION
  Loads desktop/proof/runtime-manifest.json and, for each of the five topology
  components, verifies:
    - executable presence in the proof layout (bundled binary/script or Electron
      embedded node),
    - startup-command tokens do not resolve a user-installed runtime or Docker under a
      stripped PATH (system dirs only) and match the recorded manifest,
    - SHA-256 evidence hashes match the recorded manifest (readiness, shutdown,
      runtime artifact),
    - every mandatory row is proven.

  A component is GO only when every mandatory row is proven, every evidence hash
  validates and measured environment facts match the recorded manifest. Overall verdict
  is GO only when all five components are GO; otherwise NO-GO with a nonzero exit code.
  Any absent or unproven mandatory field is FAIL (fail-closed, D-41-04/D-41-06,
  T-41-03-01).

.PARAMETER Manifest
  Path to the runtime manifest JSON (default desktop/proof/runtime-manifest.json).

.PARAMETER OutFile
  Where to write the machine-readable evidence report JSON (default
  desktop/proof/logs/runtime-feasibility-evidence.json).

.EXAMPLE
  powershell -File desktop/proof/scripts/verify-bundled-runtime.ps1
#>
[CmdletBinding()]
param(
  [string]$ManifestPath = "desktop/proof/runtime-manifest.json",
  [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$scriptDir = $PSScriptRoot
$proofRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$repoRoot = (Resolve-Path (Join-Path $proofRoot "..\..")).Path

if (-not [string]::IsNullOrWhiteSpace($ManifestPath) -and -not [System.IO.Path]::IsPathRooted($ManifestPath)) {
  $ManifestPath = Join-Path $repoRoot $ManifestPath
}
if (-not (Test-Path $ManifestPath)) {
  Write-Error "manifest not found: $ManifestPath"
  exit 2
}
if ([string]::IsNullOrWhiteSpace($OutFile)) {
  $OutFile = Join-Path $proofRoot "logs\runtime-feasibility-evidence.json"
}
$OutFile = [System.IO.Path]::GetFullPath($OutFile)
New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null

$manifestData = Get-Content -Raw -Path $ManifestPath | ConvertFrom-Json

function Write-Info([string]$msg) { Write-Host "[verify-bundled-runtime] $msg" }
function FailEnv([string]$msg) {
  Write-Host "[verify-bundled-runtime] FAILED: $msg" -ForegroundColor Red
  exit 1
}

# ---------------------------------------------------------------------------
# Constrained environment simulation (D-41-04): system dirs only, no user runtime,
# no Docker. The current PATH is only used to MEASURE that a recorded startup
# command currently resolves a user-installed runtime or Docker.
# ---------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($env:SystemRoot)) { $env:SystemRoot = "C:\Windows" }
$systemDirs = @(
  "$env:SystemRoot\System32",
  "$env:SystemRoot",
  "$env:SystemRoot\System32\Wbem",
  "$env:SystemRoot\System32\WindowsPowerShell\v1.0",
  "$env:SystemRoot\System32\OpenSSH"
)
$strippedPath = ($systemDirs -join ";")
$fullPath = $env:PATH

function Test-CommandResolve([string]$token, [string]$pathEnv) {
  $old = $env:PATH
  $env:PATH = $pathEnv
  try {
    # Route through cmd.exe so native stderr ("INFO: Could not find files ...")
    # is discarded and does not become a terminating error under ErrorActionPreference=Stop.
    $out = & cmd.exe /d /s /c "where.exe $token 2>nul"
    if ($LASTEXITCODE -eq 0 -and $out) { return ([string]($out | Select-Object -First 1)) }
    return $null
  } finally {
    $env:PATH = $old
  }
}

function Test-UnderPath([string]$path, [string]$root) {
  if ([string]::IsNullOrWhiteSpace($path)) { return $false }
  $p = [System.IO.Path]::GetFullPath($path).TrimEnd('\')
  $r = [System.IO.Path]::GetFullPath($root).TrimEnd('\')
  return $p.StartsWith($r + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
    $p.Equals($r, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-Sha256([string]$filePath) {
  if (-not (Test-Path -LiteralPath $filePath)) { return $null }
  try {
    return (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLower()
  } catch {
    return $null
  }
}

# Docker must be unreachable under the stripped PATH (simulated packaged machine).
$dockerStripped = Test-CommandResolve "docker" $strippedPath
$dockerSimulatedUnavailable = ($null -eq $dockerStripped)
if (-not $dockerSimulatedUnavailable) {
  FailEnv "docker resolved under stripped PATH at '$dockerStripped' - simulation failed, Docker is reachable in the constrained environment"
}

# Electron embedded node presence inside the proof layout.
$electronDist = Join-Path $proofRoot "node_modules\electron\dist\electron.exe"
$electronDistPresent = Test-Path -LiteralPath $electronDist

$results = @()
$allGo = $true
$componentIndex = 0

foreach ($component in $manifestData.components) {
  $id = $component.id
  $componentIndex++

  $record = @{
    id = $id
    verdict = "FAIL"
    reason = ""
    mandatoryRows = @{}
    evidenceFiles = @()
    environment = @{}
    consistency = @{}
  }

  # ---- executable presence in proof layout -------------------------------
  $execKind = $component.runtime.executable.kind
  $measuredPresent = $false
  if ($execKind -eq "electron-embedded-node") {
    $measuredPresent = $electronDistPresent
  } else {
    $declared = $component.runtime.executable.declaredPath
    $resolved = Join-Path $repoRoot $declared
    $measuredPresent = (Test-Path -LiteralPath $resolved)
  }
  $recordedPresent = [bool]$component.runtime.executable.presentInProofLayout
  $record.environment.executablePresent = $measuredPresent
  $record.consistency.executablePresenceMatches = ($measuredPresent -eq $recordedPresent)

  # ---- startup-command tokens: user runtime / docker resolution -----------
  $tokens = @($component.startupCommand.tokens)
  $measuredUserRuntime = $false
  $measuredDocker = $false
  $resolvedPaths = @()
  foreach ($token in $tokens) {
    if ([string]::IsNullOrWhiteSpace($token)) { continue }
    $resolvedFull = Test-CommandResolve $token $fullPath
    if ($null -ne $resolvedFull -and -not (Test-UnderPath $resolvedFull $proofRoot)) {
      if ($token -eq "docker") { $measuredDocker = $true } else { $measuredUserRuntime = $true }
      $resolvedPaths += ("{0} -> {1}" -f $token, $resolvedFull)
    } elseif ($null -ne $resolvedFull) {
      $resolvedPaths += ("{0} -> {1} (in proof layout)" -f $token, $resolvedFull)
    }
    # Also confirm the token is NOT reachable under the stripped PATH.
    $resolvedStripped = Test-CommandResolve $token $strippedPath
    if ($null -ne $resolvedStripped) {
      $record.environment.("token:" + $token + ":strippedResolved") = $resolvedStripped
    }
  }
  $recordedUserRuntime = [bool]$component.startupCommand.resolvesUserRuntime
  $recordedDocker = [bool]$component.startupCommand.resolvesDocker
  $record.consistency.userRuntimeMatches = ($measuredUserRuntime -eq $recordedUserRuntime)
  $record.consistency.dockerMatches = ($measuredDocker -eq $recordedDocker)
  $record.environment.userRuntimeDetected = $measuredUserRuntime
  $record.environment.dockerDetected = $measuredDocker
  $record.environment.resolvedPaths = $resolvedPaths

  # ---- evidence hashes -----------------------------------------------------
  $allHashesValid = $true
  $evidenceGroups = @(
    @{ name = "readiness"; group = $component.readinessEvidence },
    @{ name = "shutdown"; group = $component.shutdownEvidence }
  )
  if ($null -ne $component.runtimeArtifact) {
    $evidenceGroups += @{ name = "runtimeArtifact"; group = $component.runtimeArtifact }
  }
  foreach ($g in $evidenceGroups) {
    $group = $g.group
    if ($null -eq $group) { continue }
    if ($group.PSObject.Properties.Name -contains "files") {
      foreach ($f in @($group.files)) {
        $absPath = Join-Path $repoRoot $f.path
        $actual = Get-Sha256 $absPath
        $expected = ([string]$f.hash).ToLower()
        $ok = ($null -ne $actual) -and ($actual -eq $expected)
        if (-not $ok) { $allHashesValid = $false }
        $record.evidenceFiles += [PSCustomObject]@{
          group = $g.name
          path = $f.path
          expectedHash = $expected
          actualHash = if ($null -eq $actual) { "MISSING" } else { $actual }
          valid = $ok
        }
      }
    } elseif ($group.PSObject.Properties.Name -contains "hash") {
      $absPath = Join-Path $repoRoot $group.path
      $actual = Get-Sha256 $absPath
      $expected = ([string]$group.hash).ToLower()
      $ok = ($null -ne $actual) -and ($actual -eq $expected)
      if (-not $ok) { $allHashesValid = $false }
      $record.evidenceFiles += [PSCustomObject]@{
        group = $g.name
        path = $group.path
        expectedHash = $expected
        actualHash = if ($null -eq $actual) { "MISSING" } else { $actual }
        valid = $ok
      }
    }
  }

  # ---- mandatory rows ------------------------------------------------------
  $mandatory = [ordered]@{
    executable = [bool]$component.runtime.executable.proven
    resourcePath = [bool]$component.resourcePath.proven
    mutableDataPath = [bool]$component.mutableDataPath.proven
    licenseRedistribution = [bool]$component.licenseRedistribution.proven
    startupCommand = [bool]$component.startupCommand.proven
    noUserRuntime = (-not $recordedUserRuntime)
    noDocker = (-not $recordedDocker)
    readinessEvidence = [bool]$component.readinessEvidence.proven
    shutdownEvidence = [bool]$component.shutdownEvidence.proven
  }
  foreach ($k in $mandatory.Keys) { $record.mandatoryRows[$k] = $mandatory[$k] }
  $mandatoryPass = $mandatory.Values -notcontains $false

  $consistencyPass =
    $record.consistency.executablePresenceMatches -and
    $record.consistency.userRuntimeMatches -and
    $record.consistency.dockerMatches

  $isGo = $mandatoryPass -and $allHashesValid -and $consistencyPass

  if ($isGo) {
    $record.verdict = "GO"
  } else {
    $record.verdict = "FAIL"
    $allGo = $false
    $failing = @()
    foreach ($k in $mandatory.Keys) { if (-not $mandatory[$k]) { $failing += $k } }
    if (-not $allHashesValid) { $failing += "evidence-hash" }
    if (-not $consistencyPass) { $failing += "environment-consistency" }
    $record.reason = ("mandatory row(s) not proven: " + ($failing -join ", "))
  }

  $results += [PSCustomObject]$record
  $stamp = if ($isGo) { "GO  " } else { "FAIL" }
  Write-Info ("{0}  {1}  {2}" -f $stamp, $id.PadRight(20), $record.reason)
}

$overallVerdict = if ($allGo) { "GO" } else { "NO-GO" }

$report = [PSCustomObject]@{
  generated = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
  manifest = $ManifestPath
  environment = [PSCustomObject]@{
    proofRoot = $proofRoot
    dockerSimulatedUnavailable = $dockerSimulatedUnavailable
    strippedPath = $strippedPath
    electronDistPresent = $electronDistPresent
  }
  components = $results
  overallVerdict = $overallVerdict
}
$report | ConvertTo-Json -Depth 12 | Set-Content -Path $OutFile -Encoding UTF8

Write-Info "evidence report written: $OutFile"
if ($overallVerdict -eq "GO") {
  Write-Info "OVERALL VERDICT: GO — every component has passing bundled-runtime evidence with matching hashes."
  exit 0
} else {
  Write-Info "OVERALL VERDICT: NO-GO — at least one component lacks bundled executable startup/readiness/shutdown evidence or resolves a user runtime/Docker (fail-closed, D-41-04/D-41-06)."
  Write-Info "failed prerequisites and replanning boundary are recorded in $ManifestPath (overall.failedPrerequisites / overall.replanningBoundary) and in 41-DECISION.md."
  exit 1
}
