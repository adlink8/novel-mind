<#
.SYNOPSIS
  Idempotently configure GitHub branch protection required checks to exactly ["ci-gate"] (06-07 / D-19).

.DESCRIPTION
  Reads the repository default branch protection via `gh api`, preserves non-required
  settings, sets required status check contexts to EXACTLY @("ci-gate"), then GETs
  readback for verification.

  On insufficient permissions (HTTP 403/401) exits nonzero with
  status blocked_external_configuration — Phase 06 must not be marked complete.

.PARAMETER Repository
  owner/repo. Defaults to $env:GITHUB_REPOSITORY or `gh repo view` detection.
  Usage: -Repository owner/repo

.PARAMETER Verify
  Only verify current required contexts equal ["ci-gate"]; do not write.
  Usage: -Verify

.PARAMETER Branch
  Branch name to protect. Default: repository default branch.

.EXAMPLE
  powershell -File scripts/ci/configure-branch-protection.ps1 -Repository adlink8/novel-mind
  powershell -File scripts/ci/configure-branch-protection.ps1 -Repository adlink8/novel-mind -Verify
#>
[CmdletBinding()]
param(
    [string]$Repository = "",
    [switch]$Verify,
    [string]$Branch = ""
)

$ErrorActionPreference = "Stop"

function Write-GateMessage {
    param([string]$Level, [string]$Message)
    Write-Host "[branch-protection][$Level] $Message"
}

function Resolve-Repository {
    param([string]$Repo)
    if ($Repo -and $Repo -match '^[^/]+/[^/]+$') {
        return $Repo
    }
    if ($env:GITHUB_REPOSITORY -and $env:GITHUB_REPOSITORY -match '^[^/]+/[^/]+$') {
        return $env:GITHUB_REPOSITORY
    }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $name = & gh repo view --json nameWithOwner --jq .nameWithOwner 2>&1
        $exit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    $msg = (
        @($name) | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
        }
    ) -join "`n"
    if ($exit -ne 0 -or -not $msg.Trim()) {
        throw "Unable to resolve repository (pass -Repository owner/repo): $msg"
    }
    return $msg.Trim()
}

function Invoke-GhApiJson {
    <#
      Runs: gh api [-X METHOD] PATH [--input FILE]
      Returns StatusCode inferred from exit + stderr/stdout, and Body text.
      Native gh stderr must not become terminating errors under Stop preference.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Body = ""
    )

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $psiArgs = @("api", "-X", $Method, $Path)
    $tmp = $null
    try {
        if ($Body) {
            $tmp = [System.IO.Path]::GetTempFileName()
            [System.IO.File]::WriteAllText($tmp, $Body, [System.Text.UTF8Encoding]::new($false))
            $psiArgs += @("--input", $tmp)
        }
        $stdout = & gh @psiArgs 2>&1
        $exit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
        if ($tmp) {
            Remove-Item -Force -ErrorAction SilentlyContinue $tmp
        }
    }

    # gh merges streams when captured; collect text (ErrorRecords + strings)
    $text = (
        @($stdout) | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.ToString()
            }
            else {
                "$_"
            }
        }
    ) -join "`n"
    $status = 0
    if ($exit -eq 0) {
        $status = 200
        # Body may still include a "gh: ..." line? normally pure JSON
        $jsonBody = $text
        # Strip leading non-json noise if any
        $idx = $jsonBody.IndexOf('{')
        $idxArr = $jsonBody.IndexOf('[')
        if ($idx -lt 0) { $idx = $idxArr }
        elseif ($idxArr -ge 0 -and $idxArr -lt $idx) { $idx = $idxArr }
        if ($idx -gt 0) {
            $jsonBody = $jsonBody.Substring($idx)
        }
        return [pscustomobject]@{
            StatusCode = $status
            Body       = $jsonBody.Trim()
            ExitCode   = $exit
            Raw        = $text
        }
    }

    # Failure: extract HTTP code from "HTTP 404" or "(HTTP 404)" patterns
    if ($text -match '\(HTTP\s+(\d{3})\)') {
        $status = [int]$Matches[1]
    }
    elseif ($text -match 'HTTP\s+(\d{3})') {
        $status = [int]$Matches[1]
    }
    else {
        $status = 500
    }

    return [pscustomobject]@{
        StatusCode = $status
        Body       = $text.Trim()
        ExitCode   = $exit
        Raw        = $text
    }
}

function Get-DefaultBranch {
    param([string]$OwnerRepo)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $name = & gh api "repos/$OwnerRepo" --jq .default_branch 2>&1
        $exit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    $msg = (
        @($name) | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
        }
    ) -join "`n"
    if ($exit -ne 0) {
        if ($msg -match 'HTTP\s*403' -or $msg -match '\(HTTP 403\)') {
            Write-GateMessage "ERROR" "blocked_external_configuration: cannot read repo metadata (HTTP 403)"
            exit 2
        }
        if ($msg -match 'HTTP\s*401' -or $msg -match '\(HTTP 401\)') {
            Write-GateMessage "ERROR" "blocked_external_configuration: unauthorized (HTTP 401)"
            exit 2
        }
        throw "Failed to read default branch: $msg"
    }
    $branch = $msg.Trim()
    if (-not $branch) {
        throw "default_branch empty for $OwnerRepo"
    }
    return $branch
}

function Get-BranchProtection {
    param(
        [string]$OwnerRepo,
        [string]$BranchName
    )
    $encoded = [uri]::EscapeDataString($BranchName)
    return Invoke-GhApiJson -Method GET -Path "repos/$OwnerRepo/branches/$encoded/protection"
}

function Get-RequiredContexts {
    param($ProtectionObject)
    if (-not $ProtectionObject) { return @() }
    $rsc = $ProtectionObject.required_status_checks
    if (-not $rsc) { return @() }
    $contexts = @()
    if ($rsc.contexts) {
        $contexts = @($rsc.contexts | ForEach-Object { [string]$_ })
    }
    elseif ($rsc.checks) {
        $contexts = @($rsc.checks | ForEach-Object { [string]$_.context })
    }
    return @($contexts | Where-Object { $_ })
}

function Build-ProtectionPutBody {
    param(
        $Existing,
        [string[]]$RequiredContexts
    )
    # GitHub PUT /branches/{branch}/protection requires a full payload.
    # Preserve non-required settings when present; otherwise use safe defaults.
    $strict = $true
    $enforceAdmins = $false
    $requiredReviews = $null
    $restrictions = $null
    $requiredLinearHistory = $false
    $allowForcePushes = $false
    $allowDeletions = $false
    $blockCreations = $false
    $requiredConversationResolution = $false

    if ($Existing) {
        if ($Existing.required_status_checks -and $null -ne $Existing.required_status_checks.strict) {
            $strict = [bool]$Existing.required_status_checks.strict
        }
        if ($Existing.enforce_admins -and $null -ne $Existing.enforce_admins.enabled) {
            $enforceAdmins = [bool]$Existing.enforce_admins.enabled
        }
        if ($Existing.required_pull_request_reviews) {
            $r = $Existing.required_pull_request_reviews
            $requiredReviews = @{
                dismiss_stale_reviews           = [bool]$r.dismiss_stale_reviews
                require_code_owner_reviews      = [bool]$r.require_code_owner_reviews
                required_approving_review_count = if ($null -ne $r.required_approving_review_count) { [int]$r.required_approving_review_count } else { 0 }
                require_last_push_approval      = [bool]$r.require_last_push_approval
            }
        }
        if ($Existing.restrictions) {
            $restrictions = @{
                users = @($Existing.restrictions.users | ForEach-Object { $_.login })
                teams = @($Existing.restrictions.teams | ForEach-Object { $_.slug })
                apps  = @($Existing.restrictions.apps | ForEach-Object { $_.slug })
            }
        }
        if ($Existing.required_linear_history -and $null -ne $Existing.required_linear_history.enabled) {
            $requiredLinearHistory = [bool]$Existing.required_linear_history.enabled
        }
        if ($Existing.allow_force_pushes -and $null -ne $Existing.allow_force_pushes.enabled) {
            $allowForcePushes = [bool]$Existing.allow_force_pushes.enabled
        }
        if ($Existing.allow_deletions -and $null -ne $Existing.allow_deletions.enabled) {
            $allowDeletions = [bool]$Existing.allow_deletions.enabled
        }
        if ($Existing.block_creations -and $null -ne $Existing.block_creations.enabled) {
            $blockCreations = [bool]$Existing.block_creations.enabled
        }
        if ($Existing.required_conversation_resolution -and $null -ne $Existing.required_conversation_resolution.enabled) {
            $requiredConversationResolution = [bool]$Existing.required_conversation_resolution.enabled
        }
    }

    $body = [ordered]@{
        required_status_checks           = @{
            strict   = $strict
            contexts = @($RequiredContexts)
        }
        enforce_admins                   = $enforceAdmins
        required_pull_request_reviews    = $requiredReviews
        restrictions                     = $restrictions
        required_linear_history          = $requiredLinearHistory
        allow_force_pushes               = $allowForcePushes
        allow_deletions                  = $allowDeletions
        block_creations                  = $blockCreations
        required_conversation_resolution = $requiredConversationResolution
    }
    return ($body | ConvertTo-Json -Depth 8 -Compress)
}

function Assert-ContextsExactlyCiGate {
    param([string[]]$Contexts)
    $normalized = @($Contexts | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($normalized.Count -ne 1 -or $normalized[0] -ne "ci-gate") {
        return $false
    }
    return $true
}

# ---- main ----
try {
    $repo = Resolve-Repository -Repo $Repository
    Write-GateMessage "INFO" "repository=$repo"

    if (-not $Branch) {
        $Branch = Get-DefaultBranch -OwnerRepo $repo
    }
    Write-GateMessage "INFO" "branch=$Branch"

    $get = Get-BranchProtection -OwnerRepo $repo -BranchName $Branch

    if ($get.StatusCode -eq 403 -or $get.StatusCode -eq 401) {
        Write-GateMessage "ERROR" "blocked_external_configuration: insufficient Administration permission to read branch protection (HTTP $($get.StatusCode))"
        Write-GateMessage "ERROR" "Grant repo Administration (or have an admin run this script). Phase 06 remains incomplete until readback is [ci-gate]."
        exit 2
    }

    $existing = $null
    if ($get.StatusCode -eq 404) {
        # Unprotected branch OR not found — treat as no protection payload
        Write-GateMessage "INFO" "no existing protection on '$Branch' (HTTP 404)"
    }
    elseif ($get.StatusCode -ge 400) {
        throw "Failed to GET branch protection: HTTP $($get.StatusCode) $($get.Body)"
    }
    else {
        $existing = $get.Body | ConvertFrom-Json
    }

    if ($Verify) {
        if (-not $existing) {
            Write-GateMessage "ERROR" "required contexts missing: branch '$Branch' is not protected (want exactly [ci-gate])"
            # Unprotected is a release-gate failure; not necessarily permission block
            exit 1
        }
        $contexts = @(Get-RequiredContexts -ProtectionObject $existing)
        Write-GateMessage "INFO" ("readback contexts=[" + ($contexts -join ", ") + "]")
        if (-not (Assert-ContextsExactlyCiGate -Contexts $contexts)) {
            Write-GateMessage "ERROR" ("required contexts must be exactly [ci-gate]; got [" + ($contexts -join ", ") + "]")
            exit 1
        }
        Write-GateMessage "OK" 'readback contexts exactly ["ci-gate"]'
        exit 0
    }

    $putBody = Build-ProtectionPutBody -Existing $existing -RequiredContexts @("ci-gate")
    $encoded = [uri]::EscapeDataString($Branch)
    $put = Invoke-GhApiJson -Method PUT -Path "repos/$repo/branches/$encoded/protection" -Body $putBody

    if ($put.StatusCode -eq 403 -or $put.StatusCode -eq 401) {
        Write-GateMessage "ERROR" "blocked_external_configuration: cannot write branch protection (HTTP $($put.StatusCode))"
        Write-GateMessage "ERROR" "Admin must grant Administration write permission or run this script. Phase 06 incomplete."
        exit 2
    }
    if ($put.StatusCode -eq 404) {
        # 404 on PUT often means free-plan public repo without branch protection API, or wrong branch
        Write-GateMessage "ERROR" "blocked_external_configuration: cannot write branch protection (HTTP 404) — plan/permission may not allow protection API"
        exit 2
    }
    if ($put.ExitCode -ne 0 -or $put.StatusCode -ge 400) {
        throw "PUT branch protection failed: HTTP $($put.StatusCode) $($put.Body)"
    }
    Write-GateMessage "INFO" "PUT protection applied"

    # Mandatory GET readback — never skip
    $readback = Get-BranchProtection -OwnerRepo $repo -BranchName $Branch
    if ($readback.StatusCode -eq 403 -or $readback.StatusCode -eq 401) {
        Write-GateMessage "ERROR" "blocked_external_configuration: cannot readback branch protection (HTTP $($readback.StatusCode))"
        exit 2
    }
    if ($readback.StatusCode -ge 400) {
        throw "GET readback failed: HTTP $($readback.StatusCode) $($readback.Body)"
    }
    $rbObj = $readback.Body | ConvertFrom-Json
    $contexts = @(Get-RequiredContexts -ProtectionObject $rbObj)
    Write-GateMessage "INFO" ("readback contexts=[" + ($contexts -join ", ") + "]")
    if (-not (Assert-ContextsExactlyCiGate -Contexts $contexts)) {
        Write-GateMessage "ERROR" ("readback required contexts must be exactly [ci-gate]; got [" + ($contexts -join ", ") + "]")
        exit 1
    }
    Write-GateMessage "OK" 'idempotent configure + readback contexts exactly ["ci-gate"]'
    exit 0
}
catch {
    Write-GateMessage "ERROR" $_.Exception.Message
    exit 1
}
