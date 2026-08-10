#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 45 Plan 45-02 Task 1: create the checksum-pinned PRIOR-VERSION desktop
  fixture (desktop/test-fixtures/prior-version/).

.DESCRIPTION
  Generates a deterministic "previous version" NovelMind app-data fixture with
  realistic mutable user data — library, chapters, analysis, visuals and
  derivatives — plus the immutable NEW-version install resources (templates,
  default assets) that an upgrade copies into app-data. Writes:

    migration.json         schemaVersion 1, runtimeVersion 0.1.0 (fixed)
    fixture-manifest.json  per-file sha256 + size inventory for EVERY fixture
                           file (data/, resources/ and migration.json) — the
                           checksum-pinned gate the upgrade verifies
                           (T-45-02-01)
    data/...               prior user data (library/chapters/analysis/visuals/
                           derivatives)
    resources/...          new-version immutable inputs for the files step

  The fixture is ASCII-only and deterministic (fixed content, fixed timestamps,
  UTF-8 without BOM) so repeated runs reproduce identical file hashes — the
  manifest can be committed and re-verified at test time.

.PARAMETER OutputDir
  Target fixture directory (default: desktop/test-fixtures/prior-version).

.EXAMPLE
  powershell -File desktop/scripts/create-upgrade-fixture.ps1
#>
[CmdletBinding()]
param(
  [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ([string]::IsNullOrEmpty($OutputDir)) {
  # $PSScriptRoot is not reliably available inside a param default expression on
  # Windows PowerShell 5.1, so derive the default in the body.
  $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path $MyInvocation.MyCommand.Path -Parent }
  $OutputDir = Join-Path (Split-Path $scriptDir -Parent) "test-fixtures\prior-version"
}

function Write-Info([string]$msg) { Write-Host "[create-upgrade-fixture] $msg" }

function Get-Sha256Hex([byte[]]$bytes) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    return ([System.BitConverter]::ToString($sha.ComputeHash($bytes)) -replace "-", "").ToLowerInvariant()
  }
  finally { $sha.Dispose() }
}

function Write-Utf8NoBom([string]$path, [string]$content) {
  $dir = Split-Path $path -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  # 45-01 deviation #2: Set-Content -Encoding utf8 writes a BOM that JSON.parse rejects.
  [System.IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))
}

# Recreate the fixture directory deterministically.
if (Test-Path $OutputDir) {
  Remove-Item -Path $OutputDir -Recurse -Force
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$dataDir = Join-Path $OutputDir "data"
$libraryDir = Join-Path $dataDir "library"
$chaptersDir = Join-Path $dataDir "chapters"
$analysisDir = Join-Path $dataDir "analysis"
$visualsDir = Join-Path $dataDir "visuals"
$derivativesDir = Join-Path $dataDir "derivatives"
$templatesDir = Join-Path $OutputDir "resources\templates"
$assetsDir = Join-Path $OutputDir "resources\assets"

# ---- prior user data (schemaVersion 1 layout) ----
Write-Utf8NoBom (Join-Path $libraryDir "novels.json") @'
[
  { "id": "novel-001", "title": "Whispers of the Star Sea", "genre": "Sci-Fi", "createdAt": "2026-06-01T08:00:00.000Z", "wordCount": 12800, "status": "in-progress" },
  { "id": "novel-002", "title": "Fogbound Notebook", "genre": "Mystery", "createdAt": "2026-06-15T09:30:00.000Z", "wordCount": 9400, "status": "draft" }
]
'@

Write-Utf8NoBom (Join-Path $chaptersDir "chapter-001.md") @'
# Chapter One - Echoes

Deep in the night library, only the last lamp still burned.

He closed the yellowed manuscript. On the cover, in faded ink: "Whispers of the Star Sea".

> Every memory is a star; the ones that have gone out are merely hidden from view.

## Fragment

Rain tapped against the window like an old typewriter dictating a conversation no one was having.
'@

Write-Utf8NoBom (Join-Path $chaptersDir "chapter-002.md") @'
# Chapter Two - Blackout

The power failed at nine thirty, plunging the reading room into silence.

A single green light from the fire exit traced the shelves like a compass needle.

## Fragment

She wrote by the glow of her phone, one sentence at a time, as if the dark were taking dictation.
'@

Write-Utf8NoBom (Join-Path $analysisDir "analysis-novel-001.json") @'
{
  "novelId": "novel-001",
  "generatedAt": "2026-07-01T12:00:00.000Z",
  "structure": { "threeAct": { "setup": "book-1", "confrontation": "book-2", "resolution": "book-3" } },
  "pacing": [ { "chapter": 1, "tension": 0.6 }, { "chapter": 2, "tension": 0.8 } ],
  "characters": [ { "name": "Lin Zhe", "role": "protagonist" } ]
}
'@

# 1x1 transparent PNG — the fixture "visual" asset (deterministic bytes).
New-Item -ItemType Directory -Path $visualsDir -Force | Out-Null
$transparentPng = [Convert]::FromBase64String(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
[System.IO.File]::WriteAllBytes((Join-Path $visualsDir "cover-001.png"), $transparentPng)

Write-Utf8NoBom (Join-Path $derivativesDir "timeline-novel-001.json") @'
{
  "novelId": "novel-001",
  "events": [
    { "at": "chapter-001", "event": "manuscript discovered", "impact": "opening" },
    { "at": "chapter-002", "event": "library blackout", "impact": "turn" }
  ]
}
'@

# ---- new-version immutable install resources (upgrade inputs) ----
Write-Utf8NoBom (Join-Path $templatesDir "outline-template.json") @'
{
  "id": "outline-template-01",
  "name": "Three-Act Outline",
  "fields": ["incitingIncident", "midpoint", "climax", "resolution"]
}
'@

# 1x1 red PNG — the new-version default cover asset.
New-Item -ItemType Directory -Path $assetsDir -Force | Out-Null
$redPng = [Convert]::FromBase64String(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
[System.IO.File]::WriteAllBytes((Join-Path $assetsDir "default-cover.png"), $redPng)

# ---- version metadata: prior schemaVersion 1 / runtimeVersion 0.1.0 ----
Write-Utf8NoBom (Join-Path $OutputDir "migration.json") @'
{
  "layoutVersion": 1,
  "schemaVersion": 1,
  "runtimeVersion": "0.1.0",
  "committedAt": "2026-08-01T00:00:00.000Z",
  "txnId": "upgrade-fixture-v1"
}
'@

# ---- checksum-pinned manifest (every file except the manifest itself) ----
$entries = @()
$totalBytes = 0
$files = Get-ChildItem -Path $OutputDir -Recurse -File | Where-Object { $_.Name -ne "fixture-manifest.json" } | Sort-Object FullName
foreach ($file in $files) {
  $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
  $rel = $file.FullName.Substring($OutputDir.Length + 1).Replace("\", "/")
  $entries += [PSCustomObject]@{ path = $rel; sha256 = Get-Sha256Hex $bytes; size = $bytes.Length }
  $totalBytes += $bytes.Length
}

$manifest = [ordered]@{
  version = 1
  schemaVersion = 1
  runtimeVersion = "0.1.0"
  createdAt = "2026-08-01T00:00:00.000Z"
  fileCount = $entries.Count
  totalBytes = $totalBytes
  files = @($entries | Sort-Object path)
}
# ConvertTo-Json on the ordered hashtable; depth high enough for the files array.
$manifestJson = $manifest | ConvertTo-Json -Depth 4
Write-Utf8NoBom (Join-Path $OutputDir "fixture-manifest.json") $manifestJson

Write-Info "fixture written to $OutputDir ($($entries.Count) files, $totalBytes bytes)"
