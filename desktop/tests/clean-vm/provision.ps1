#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 45 Plan 45-03 Task 1: provision the local machine boundary for the
  qualification run.

.DESCRIPTION
  No clean Windows VM was available or authorized for this wave, so the run
  simulates a clean first-run machine on the developer workstation (the honest
  approximation recorded in the qualification manifest):

    1. TIGHTENED PATH — every binary the packaged app must NOT depend on is
       removed from the child PATH: Node, npm/npx, Python/venv, Docker,
       PostgreSQL/psql, uvicorn, pg_dump etc. The qualification harness then
       fails if any script requires one of these to start the packaged app.
    2. ISOLATED USER DATA — NOVELMIND_USER_DATA points at a per-run temp dir so
       first-run data never touches the developer's real %APPDATA%.
    3. BOUNDARY REPORT — the resolved PATH and machine identity are written to
       the results dir as evidence (T-45-03-01/02: redacted, no personal data).

  This is NOT pristine-VM evidence (D-45-07/D-45-09). The manifest marks
  clean_vm=false; every UAT row records pass/fail on this machine and the
  missing clean-VM execution remains a blocking release gap.

.PARAMETER Manifest
  Path to the qualification manifest (desktop/tests/fixtures/qualification-manifest.json).
.PARAMETER ResultsDir
  Where evidence (machine-boundary.json) is written. Defaults to the clean-vm/results dir.

.EXAMPLE
  powershell -File desktop/tests/clean-vm/provision.ps1 -Manifest desktop/tests/fixtures/qualification-manifest.json
#>
[CmdletBinding()]
param(
  [string]$Manifest,
  [string]$ResultsDir
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
if ([string]::IsNullOrWhiteSpace($Manifest)) {
  $Manifest = Join-Path $repoRoot "desktop\tests\fixtures\qualification-manifest.json"
}
if ([string]::IsNullOrWhiteSpace($ResultsDir)) {
  $ResultsDir = Join-Path $PSScriptRoot "results"
}
New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null

function Write-Step([string]$msg) { Write-Host "[provision] $msg" }

# ---- 0. manifest presence (plan Task 1 automated gate) ----------------------
if (-not (Test-Path $Manifest)) {
  throw "qualification manifest missing at $Manifest (plan 45-03 Task 1 gate)"
}
$manifestData = Get-Content $Manifest -Raw | ConvertFrom-Json
Write-Step "manifest loaded: $($manifestData.qualificationScope) (clean_vm=$($manifestData.machine.clean_vm))"

# ---- 1. tightened PATH -------------------------------------------------------
# Tokens that would mask a packaged-runtime dependency. The packaged app must
# run WITHOUT any of these on PATH.
$forbiddenTokens = @(
  "node", "npm", "npx", "python", "python3", "py",
  "docker", "docker-compose", "podman",
  "psql", "postgres", "pg_ctl", "pg_dump",
  "uvicorn", "venv", "Scripts", "Pip", "ChocolateyBin"
)
$originalPath = $env:PATH
$filtered = @()
foreach ($entry in $originalPath.Split([System.IO.Path]::PathSeparator)) {
  if ([string]::IsNullOrWhiteSpace($entry)) { continue }
  $lower = $entry.ToLowerInvariant()
  $skip = $false
  foreach ($token in $forbiddenTokens) {
    if ($lower -match [regex]::Escape($token.ToLowerInvariant())) { $skip = $true; break }
  }
  if (-not $skip) { $filtered += $entry }
}
$tightPath = $filtered -join [System.IO.Path]::PathSeparator

# Sanity: the packaged exe itself must still be findable on the disk, but the
# PATH we export must not contain Node/Python/Docker directories.
Write-Step "PATH tightened from $($originalPath.Split([System.IO.Path]::PathSeparator).Count) to $($filtered.Count) entries"

# ---- 2. isolated user data ----------------------------------------------------
$userDataDir = Join-Path (Join-Path $env:TEMP "novelmind-qual") ("run-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $userDataDir | Out-Null

# ---- 3. machine boundary report ----------------------------------------------
$boundary = @{
  schemaVersion    = 1
  provisionedAt    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  machine          = $manifestData.machine
  tightenedPath    = @{ entries = $filtered.Count; excludedTokens = $forbiddenTokens }
  userDataIsolation = @{ override = "NOVELMIND_USER_DATA"; dir = $userDataDir; insideTemp = $userDataDir.StartsWith($env:TEMP) }
  limitations      = @(
    "Local workstation, not a pristine Windows VM snapshot — developer-machine smoke, NOT clean-VM evidence (D-45-07).",
    "Packaged adapter auto-wiring in the main process remains a documented post-45 prerequisite; the renderer is served from the bundled tree through the packaged exe's embedded Node via the NOVELMIND_RENDERER_URL seam."
  )
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$boundaryPath = Join-Path $ResultsDir "machine-boundary.json"
[System.IO.File]::WriteAllText($boundaryPath, ($boundary | ConvertTo-Json -Depth 5), $utf8NoBom)
Write-Step "boundary report written to $boundaryPath"

# ---- 4. emit the child environment -------------------------------------------
# Writes an env file that run-qualification.ps1 sources for child processes.
$envFile = Join-Path $ResultsDir "qualification.env.json"
$envPayload = @{
  PATH            = $tightPath
  NOVELMIND_USER_DATA = $userDataDir
  NOVELMIND_PACKAGED_EXE = (Join-Path $repoRoot "desktop\dist\win-unpacked\NovelMind.exe")
}
[System.IO.File]::WriteAllText($envFile, ($envPayload | ConvertTo-Json), $utf8NoBom)
Write-Step "child env written to $envFile"
Write-Step "PROVISION DONE"
