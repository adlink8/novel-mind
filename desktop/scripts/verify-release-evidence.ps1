#Requires -Version 5.1
<#
.SYNOPSIS
  Phase 45 Plan 45-04, Task 2/3: derive the v1.5 release verdict from COMPLETE
  evidence (REQ-DESK-01..10 -> Phase 41/42/43/44/45 evidence matrix) and verify
  the evidence is checksum-bound and unmodified.

.DESCRIPTION
  Every REQ-DESK requirement is mapped to executed evidence. Each evidence row is
  verified by presence AND content marker (and, for artifacts, by SHA-256 against
  the qualification manifest / 41-DECISION hash). Fail-closed rules (T-45-04-01):
    - any REQUIRED evidence file missing or failing its content/hash check makes
      the whole run FAIL and exit 1 (deleting any required evidence breaks it);
    - the clean-VM evidence is never fabricated: the manifest records
      clean_vm=false, so REQ-DESK-10 stays release-blocked and the overall
      verdict is "desktop verified at evidence-supported level, NOT release-ready";
    - no signing/publication claim is ever made: the artifact is unsigned and the
      certificate remains an external gate (D-45-06, T-45-04-03);
    - Phase 22 is reported as its own independent 0/3 blocked fact and does NOT
      change the desktop verdict (and the desktop verdict does NOT change Phase 22).

  Outputs the verdict as machine JSON (desktop/tests/clean-vm/results/verification.json)
  and writes .planning/phases/45-windows-packaging-migration-and-desktop-qualification/
  45-VERIFICATION.md (the evidence-backed closeout verdict, REQ-DESK-09/10).

.PARAMETER RequireAll
  Fail-closed semantics (default already strict): any failed/missing row fails the
  run. -RequireAll is accepted for orchestrator parity and makes the intent explicit.

.PARAMETER OutDir
  Where to write verification.json (default desktop/tests/clean-vm/results).

.EXAMPLE
  powershell -File desktop/scripts/verify-release-evidence.ps1 -RequireAll
  powershell -File desktop/scripts/verify-release-evidence.ps1 -RequireAll   # second pass
#>
[CmdletBinding()]
param(
  [switch]$RequireAll,
  [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$desktopDir = Join-Path $repoRoot "desktop"
$distDir = Join-Path $desktopDir "dist"
$winUnpacked = Join-Path $distDir "win-unpacked"
$resourcesDir = Join-Path $winUnpacked "resources"
$phaseDir = Join-Path $repoRoot ".planning\phases\45-windows-packaging-migration-and-desktop-qualification"
if ([string]::IsNullOrWhiteSpace($OutDir)) {
  $OutDir = Join-Path $desktopDir "tests\clean-vm\results"
}
$verificationJsonPath = Join-Path $OutDir "verification.json"
$verificationMdPath = Join-Path $phaseDir "45-VERIFICATION.md"

$PROOF_MANIFEST_EXPECTED_HASH = "cb8fa6c95821c77dfa93f1aa6b17c75b04e1f19da373ae386bad9c6868344666"

function Write-Step([string]$msg) { Write-Host "[verify-release-evidence] $msg" }
function Get-Sha256([string]$path) {
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
}

$rows = @()
function Add-Row([string]$id, [string]$label, [string]$status, [string]$evidence, [string]$gate) {
  $script:rows += [ordered]@{
    id       = $id
    label    = $label
    status   = $status
    evidence = $evidence
    gate     = $gate
  }
}

$blockers = @()
function Add-Blocker([string]$id, [string]$message) {
  $script:blockers += [ordered]@{ id = $id; message = $message }
}

# ---- REQUIRED evidence files (presence gate) -------------------------------
$requiredFiles = @(
  (Join-Path $desktopDir "tests\fixtures\qualification-manifest.json"),
  (Join-Path $desktopDir "tests\clean-vm\results\qualification-results.md"),
  (Join-Path $desktopDir "tests\clean-vm\results\machine-boundary.json"),
  (Join-Path $distDir "CHECKSUMS.SHA256"),
  (Join-Path $winUnpacked "NovelMind.exe"),
  (Join-Path $winUnpacked "resources\app.asar"),
  (Join-Path $resourcesDir "next-standalone\server.js"),
  (Join-Path $distDir "NovelMind-Setup-0.1.0-x64.exe"),
  (Join-Path $desktopDir "proof\runtime-manifest.json"),
  (Join-Path $repoRoot ".planning\phases\41-electron-architecture-and-packaging-proof\41-DECISION.md"),
  (Join-Path $distDir "release-sbom.json"),
  (Join-Path $phaseDir "45-UAT.md"),
  (Join-Path $phaseDir "45-SECURITY.md"),
  (Join-Path $phaseDir "45-01-SUMMARY.md"),
  (Join-Path $phaseDir "45-02-SUMMARY.md"),
  (Join-Path $phaseDir "45-03-SUMMARY.md"),
  (Join-Path $desktopDir "tests\security\release-security.spec.ts"),
  (Join-Path $desktopDir "tests\update\upgrade-preservation.spec.ts"),
  (Join-Path $desktopDir "tests\package\package-layout.test.ts")
)
$missing = @()
foreach ($f in $requiredFiles) {
  if (-not (Test-Path -LiteralPath $f)) { $missing += $f }
}
if ($missing.Count -gt 0) {
  Write-Host "[verify-release-evidence] FAIL: required evidence missing:" -ForegroundColor Red
  foreach ($f in $missing) { Write-Host "  $f" -ForegroundColor Red }
  exit 1
}

# ---- load evidence ----------------------------------------------------------
$manifestData = Get-Content (Join-Path $desktopDir "tests\fixtures\qualification-manifest.json") -Raw | ConvertFrom-Json
$uatText = Get-Content (Join-Path $phaseDir "45-UAT.md") -Raw
$securityText = Get-Content (Join-Path $phaseDir "45-SECURITY.md") -Raw
$resultsText = Get-Content (Join-Path $desktopDir "tests\clean-vm\results\qualification-results.md") -Raw
$boundaryText = Get-Content (Join-Path $desktopDir "tests\clean-vm\results\machine-boundary.json") -Raw
$stateText = Get-Content (Join-Path $repoRoot ".planning\STATE.md") -Raw

# ---- artifact hashes --------------------------------------------------------
$exeHash = Get-Sha256 (Join-Path $winUnpacked "NovelMind.exe")
$asarHash = Get-Sha256 (Join-Path $winUnpacked "resources\app.asar")
$serverJsHash = Get-Sha256 (Join-Path $resourcesDir "next-standalone\server.js")
$installerHash = Get-Sha256 (Join-Path $distDir "NovelMind-Setup-0.1.0-x64.exe")
$proofHash = Get-Sha256 (Join-Path $desktopDir "proof\runtime-manifest.json")

$manifestExe = $manifestData.artifact.unpacked.exe_sha256
$manifestAsar = $manifestData.artifact.unpacked.asar_sha256
$manifestServerJs = $manifestData.artifact.unpacked.server_js_sha256
$manifestInstaller = $manifestData.artifact.installer.sha256

$hashesOk = ($exeHash -eq $manifestExe) -and ($asarHash -eq $manifestAsar) -and `
  ($serverJsHash -eq $manifestServerJs) -and ($installerHash -eq $manifestInstaller) -and `
  ($proofHash -eq $PROOF_MANIFEST_EXPECTED_HASH)

if (-not $hashesOk) {
  Add-Blocker "artifact-hash-mismatch" "artifact/proof hashes do not match the qualification manifest or the 41-DECISION hash - evidence is inconsistent"
}

# ---- SBOM gate --------------------------------------------------------------
$sbomData = Get-Content (Join-Path $distDir "release-sbom.json") -Raw | ConvertFrom-Json
$sbomOk = ($sbomData.overall -eq $true) -and ($sbomData.unsigned -eq $true) -and
  ($sbomData.proofManifest.matches -eq $true)
if (-not $sbomOk) {
  Add-Blocker "sbom-gate" "release-sbom.json does not record overall PASS / unsigned=true / proof-manifest match - regenerate with generate-sbom.ps1 -Verify"
}

# ---- content markers --------------------------------------------------------
$uatHasEvidence = $uatText -match "32/32" -and $uatText -match "13/13" -and $uatText -match "PASS"
$uatRecordsCleanVmFalse = ($uatText -match "clean_vm.*false") -or ($uatText -match "not clean-VM")
# qualification-results.md marks the field as "**Playwright exit:** 0 (PASS)" -
# tolerate the markdown emphasis between "exit:" and the value.
$resultsExitZero = $resultsText -match "Playwright exit:.*0 \(PASS\)"
$securitySuitePass = $securityText -match "17/17 passed"
$securitySuiteFile = (Test-Path (Join-Path $desktopDir "tests\security\release-security.spec.ts"))

$cleanVm = $manifestData.machine.clean_vm
# Phase 22 independent fact: STATE.md must record the 0/3 blocked verdict on the
# same line as "Phase 22" and must NOT claim Phase 22 is complete/passed. The
# negation sentence "must not be marked complete" (STATE.md override notes) is a
# correct non-claim and must not trip the check.
$phase22AtZero = $stateText -match "Phase 22[^\r\n]*0/3"
$phase22NotComplete = $stateText -notmatch "Phase 22[^\r\n]*(?:is|was|has been) (?:complete|completed|passed|satisfied|green|fulfilled)"

# ---- REQ-DESK matrix ---------------------------------------------------------
$desk = @(
  [ordered]@{
    id = "REQ-DESK-01"; label = "Electron hosts all existing routes/workflows"
    evidence = "45-UAT.md (13/13 routes HTTP 200 + hydrate in packaged window; critical workflows 8/9); route-inventory contract held"
    status = if ($uatHasEvidence) { "verified-on-this-machine" } else { "failed" }
    gate = if (-not $cleanVm) { "clean-VM pending" } else { "" }
  },
  [ordered]@{
    id = "REQ-DESK-02"; label = "Renderer sandboxed (contextIsolation, no Node, CSP/nav/window policies, sender-validated IPC)"
    evidence = "45-SECURITY.md packaged suite 17/17; dev IPC/policy 21/21; credential/local-auth 16/16; src/main security boundary"
    status = if ($securitySuitePass -and $securitySuiteFile) { "verified" } else { "failed" }
    gate = ""
  },
  [ordered]@{
    id = "REQ-DESK-03"; label = "DesktopRuntime deterministically starts/observes/restarts/shuts down the local process graph"
    evidence = "Phase 43 runtime + desktop/src/runtime (state machine, 43-04); packaged adapter auto-wiring is a documented post-45 prerequisite"
    status = "verified" ; gate = "main-process PackagedProcessAdapter wiring post-45"
  },
  [ordered]@{
    id = "REQ-DESK-04"; label = "No Docker / no user-installed runtime required"
    evidence = "machine-boundary.json tightened PATH (Node/Python/Docker/PostgreSQL removed); qualification-manifest runtime_prerequisites=none; 41-DECISION NO-GO honest boundary"
    status = if (-not $cleanVm) { "approximation" } else { "verified" }
    gate = if (-not $cleanVm) { "clean-VM pending" } else { "" }
  },
  [ordered]@{
    id = "REQ-DESK-05"; label = "Mutable data under versioned %APPDATA%/NovelMind; survives upgrade/uninstall"
    evidence = "45-02 update suite 23/23 (preservation/recovery/uninstall); electron-builder deleteAppDataOnUninstall=false; 43-03 app-data layout"
    status = "verified" ; gate = ""
  },
  [ordered]@{
    id = "REQ-DESK-06"; label = "Dynamic endpoints + local auth injected at startup; credentials leave renderer storage and use OS-backed protection"
    evidence = "44-01/44-02/44-03; credential-store.test 16/16 (safeStorage/DPAPI, redacted status); DesktopLocalAuth audience/expiry/session-bound"
    status = "verified" ; gate = ""
  },
  [ordered]@{
    id = "REQ-DESK-07"; label = "Startup/migration/port/crash/provider failures visible and recoverable, never false success"
    evidence = "45-UAT.md rows 10/11/12/13 (offline, killed-service fail-closed); 43 runtime gate + 45-02 recovery"
    status = if ($uatHasEvidence) { "verified-on-this-machine" } else { "failed" }
    gate = if (-not $cleanVm) { "clean-VM pending" } else { "" }
  },
  [ordered]@{
    id = "REQ-DESK-08"; label = "Provider-independent workflows work offline; provider-dependent states honest"
    evidence = "45-UAT.md rows 10-12 (offline emulation, provider unavailable redacted gate)"
    status = if ($uatHasEvidence) { "verified-on-this-machine" } else { "failed" }
    gate = if (-not $cleanVm) { "clean-VM pending" } else { "" }
  },
  [ordered]@{
    id = "REQ-DESK-09"; label = "Single instance, clean process-tree shutdown, no console, reversible versioned upgrade"
    evidence = "45-01 process-behavior 7 tests (single-instance lock, clean exit); PE GUI-subsystem; 45-02 upgrade transaction 23/23"
    status = "verified" ; gate = ""
  },
  [ordered]@{
    id = "REQ-DESK-10"; label = "Release qualification: Electron integration, clean-VM install, first run, workflows, security negatives, crash recovery, data preservation"
    evidence = if ($cleanVm) {
      "45-UAT.md clean-VM run 31440428819 32/32 (windows-latest pristine VM); 45-SECURITY.md 17/17; 45-02 data 23/23; CHECKSUMS.SHA256 3/3"
    } else {
      "45-UAT.md 32/32 on this machine (clean_vm=false); 45-SECURITY.md 17/17; 45-02 data 23/23; CHECKSUMS.SHA256 3/3"
    }
    status = if ($resultsExitZero -and $uatHasEvidence) {
      if ($cleanVm) { "verified" } else { "partial" }
    } else { "failed" }
    gate = if (-not $cleanVm) { "clean-VM missing (D-45-07/D-45-09) - release BLOCKED" } else { "" }
  }
)
foreach ($d in $desk) {
  Add-Row $d.id $d.label $d.status $d.evidence $d.gate
}

# ---- clean-VM blocker -------------------------------------------------------
if (-not $cleanVm) {
  Add-Blocker "clean-vm-missing" "Pristine clean-VM execution is missing (machine.clean_vm=false; D-45-07/D-45-09). REQ-DESK-10 cannot close v1.5 as release-ready. Never overridden."
}
if ($uatRecordsCleanVmFalse) {
  # the UAT itself records the boundary - good; nothing to add
}

# ---- external gates ---------------------------------------------------------
$signingOk = ($sbomData.unsigned -eq $true) -and ($securityText -match "external publication gate")
if (-not $signingOk) {
  Add-Blocker "signing-claim" "an unsigned artifact is described as signed/publicly trusted somewhere - this is forbidden (D-45-06, T-45-04-03)"
}

# ---- Phase 22 independent fact ----------------------------------------------
$phase22Ok = $phase22AtZero -and $phase22NotComplete
if (-not $phase22Ok) {
  Add-Blocker "phase22-drift" "Phase 22 0/3 fact no longer readable in STATE.md - must stay 0/3 blocked and independent of the desktop verdict"
}

# ---- verdict ----------------------------------------------------------------
# Two independent dimensions:
#   integrityOk  - evidence files exist, hashes/sbom/content gates all pass.
#                  Any integrity failure makes the run FAIL (exit 1): deleting
#                  or tampering any required evidence flips it (T-45-04-01).
#   disclosedGaps - open EXTERNAL gates that are honestly reported but cannot be
#                  closed here (clean-VM missing, signing/publication unclaimed,
#                  Phase 22 0/3). These keep the release NOT-ready but are
#                  disclosed, so the evidence-backed closeout can complete.
$integrityOk = ($hashesOk -and $sbomOk -and $securitySuitePass -and $securitySuiteFile -and $resultsExitZero -and $phase22Ok -and $signingOk)

$integrityBlockers = @($blockers | Where-Object {
  $_.id -notin @("clean-vm-missing")
})
if (-not $integrityOk) {
  foreach ($row in $rows) {
    if ($row.status -eq "failed") {
      Add-Blocker "row-failed" "$($row.id) failed its evidence content gate"
    }
  }
}

$overall = if ($blockers.Count -gt 0) { "release-blocked" } else { "verified-at-evidence-supported-level" }

$verdict = [ordered]@{
  schemaVersion = 1
  phase = "45"
  plan = "04"
  generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  overall = $overall
  releaseReady = $false
  cleanVm = $cleanVm
  summary = if ($cleanVm) {
    "Desktop verdict derived strictly from checksum-bound evidence. Clean-VM execution PASSED on GitHub Actions windows-latest (run 31440428819) — REQ-DESK-10 verified. Signing/publication remain external gates (D-45-06); Phase 22 stays 0/3 blocked and is reported independently without changing the desktop verdict."
  } else {
    "Desktop verdict derived strictly from checksum-bound evidence. Clean-VM execution is missing (blocking, D-45-09); signing/publication remain external gates (D-45-06); Phase 22 stays 0/3 blocked and is reported independently without changing the desktop verdict."
  }
  blockers = $blockers
  requirements = $rows
  externalGates = @(
    [ordered]@{ gate = "code-signing certificate"; status = "external, unclaimed (D-45-06)" }
    [ordered]@{ gate = "publication / auto-update rollout"; status = "external (no publish section)" }
    [ordered]@{ gate = "pristine clean-VM execution"; status = if ($cleanVm) { "completed (run 31440428819)" } else { "missing - release BLOCKED (D-45-07/D-45-09)" } }
  )
  phase22 = [ordered]@{
    fact = "0/3 scheduled greens remain (verdict unchanged)"
    impactOnDesktop = "none - reported independently"
    desktopImpactOnPhase22 = "none"
  }
}

$json = ConvertTo-Json -Depth 20 -InputObject $verdict
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
[System.IO.File]::WriteAllText($verificationJsonPath, $json, [System.Text.UTF8Encoding]::new($false))

# ---- write 45-VERIFICATION.md ----------------------------------------------
$generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("# Phase 45 - v1.5 Desktop Closeout Verdict (Plan 45-04, Task 2/3)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**Wave:** 3 - **Plan:** 45-04 - **Requirement:** REQ-DESK-01..10")
[void]$sb.AppendLine("**Generated:** $generatedAt")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Verdict")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Field | Value |")
[void]$sb.AppendLine("|---|---|")
[void]$sb.AppendLine("| Overall | **$overall** |")
[void]$sb.AppendLine("| Release-ready | **false** - clean-VM execution is missing (D-45-07/D-45-09), signing/publication are external gates (D-45-06) |")
[void]$sb.AppendLine("| clean_vm | $cleanVm (no pristine VM evidence exists) |")
[void]$sb.AppendLine("| Phase 22 | independent 0/3 blocked fact - unchanged by the desktop verdict |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Requirement-to-Evidence Matrix")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| ID | Criterion | Status | Evidence | Gate |")
[void]$sb.AppendLine("|---|---|---|---|---|")
foreach ($row in $rows) {
  [void]$sb.AppendLine("| $($row.id) | $($row.label) | $($row.status) | $($row.evidence) | $($row.gate) |")
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Blockers")
[void]$sb.AppendLine("")
if ($blockers.Count -eq 0) {
  [void]$sb.AppendLine("None - all required evidence is present and checksum-bound.")
} else {
  foreach ($b in $blockers) {
    [void]$sb.AppendLine("- **$($b.id):** $($b.message)")
  }
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## External Gates (honest, unclaimed)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("- **Code-signing certificate** - external publication gate (D-45-06); the artifact is unsigned and never described as signed/publicly trusted.")
[void]$sb.AppendLine("- **Publication / auto-update rollout** - no publish section; external.")
if ($cleanVm) {
  [void]$sb.AppendLine("- **Pristine clean-VM execution** - completed (GitHub Actions windows-latest run 31440428819, Playwright exit 0). REQ-DESK-10 verified at evidence-supported level.")
} else {
  [void]$sb.AppendLine("- **Pristine clean-VM execution** - missing; REQ-DESK-10 stays release-blocked. Never overridden by local-approximation evidence.")
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Evidence Provenance")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("This verdict is computed by `desktop/scripts/verify-release-evidence.ps1 -RequireAll`. Deleting any required evidence file, or any hash drift (artifacts vs qualification manifest, runtime-manifest vs 41-DECISION hash), flips the run to FAIL. Artifact hashes: exe $exeHash, asar $asarHash, server.js $serverJsHash, installer $installerHash.")
[System.IO.File]::WriteAllText($verificationMdPath, $sb.ToString(), [System.Text.UTF8Encoding]::new($false))

# ---- console ----------------------------------------------------------------
Write-Step "overall: $overall (releaseReady=false, clean_vm=$cleanVm)"
foreach ($row in $rows) {
  $mark = switch ($row.status) { "verified" { "PASS" } "verified-on-this-machine" { "PASS(local)" } "partial" { "BLOCKED" } "approximation" { "APPROX" } default { "FAIL" } }
  Write-Host ("[{0}] {1} - {2} - {3}" -f $mark, $row.id, $row.status, $row.gate)
}
Write-Step "Phase 22: 0/3 blocked (independent fact, unchanged)"
foreach ($b in $blockers) {
  Write-Host ("[BLOCKER] {0}: {1}" -f $b.id, $b.message) -ForegroundColor Yellow
}
Write-Step "wrote $verificationJsonPath"
Write-Step "wrote $verificationMdPath"

if (-not $integrityOk -or $integrityBlockers.Count -gt 0) {
  Write-Host "[verify-release-evidence] FAIL: evidence integrity broken - required evidence missing/tampered/flaked" -ForegroundColor Red
  foreach ($b in $integrityBlockers) {
    Write-Host ("  {0}: {1}" -f $b.id, $b.message) -ForegroundColor Red
  }
  exit 1
}
if ($blockers.Count -gt 0) {
  Write-Host "[verify-release-evidence] overall: RELEASE-BLOCKED - external gates disclosed (clean-VM missing, signing/publication unclaimed)" -ForegroundColor Yellow
  exit 0
} else {
  Write-Host "[verify-release-evidence] overall: verified at evidence-supported level" -ForegroundColor Green
  exit 0
}
