# 06-07 Summary — ci-gate Aggregate, Branch Protection & Phase Release Gate

**Status:** COMPLETE  
**Date:** 2026-07-12  
**Plan:** `.planning/phases/06-automated-quality-ci/06-07-PLAN.md`  
**Decisions:** D-19 (also closes D-13 required-check surface)

## What Was Done

### Slice 1 — Fail-closed `ci-gate` aggregate

- Added `scripts/ci/ci-gate.py`: event-aware required producer matrix for
  `pull_request` / `push` / `schedule` / `workflow_dispatch` (+ optional nightly on dispatch).
- Fail closed on: `failure`, `cancelled`, `timed_out`, unexpected `skipped`, missing producer,
  missing/stale artifact, hash mismatch, schema mismatch.
- Success only when every event-required producer reports `success`.
- Does **not** re-run tests or re-interpret quality scores (aggregate only).
- Wired single stable job in `.github/workflows/ci.yml`:
  - job id + `name:` exactly `ci-gate` (branch protection required context)
  - `if: always()`
  - `needs` all producers (not `alert`)
  - writes producer-results envelope from `needs.*.result` and runs `ci-gate.py`
- Tests: `tests/ci/test_ci_gate.py` (success / failed / cancelled / unexpected skip /
  missing / timeout / hash / schema / stale / main live / schedule nightly)

### Slice 2 — Branch protection + Phase 06 release gate

- `scripts/ci/configure-branch-protection.ps1`
  - Resolves owner/repo via `-Repository`, `$env:GITHUB_REPOSITORY`, or `gh repo view`
  - Reads default branch; GET protection; preserves non-required settings
  - PUT required status check contexts to **exactly** `["ci-gate"]`
  - Mandatory GET readback after write
  - `-Verify` read-only mode
  - Exit `2` + `blocked_external_configuration` on HTTP 401/403 (and 404 on write when API unavailable)
- `scripts/ci/verify-release-gate.py`
  - Checks all seven plan `*-SUMMARY.md` files
  - Required on-disk evidence trails for 06-01..06-07
  - Signed/policy files (coverage, baseline, service-lock, rag-quality policy, fixtures, calibration)
  - Workflow has `ci-gate` job with `always()`
  - Flake / retention / timeout policy locks
  - Live branch protection verify via PowerShell script; blocked_external fails the phase
- Live configure on `adlink8/novel-mind` default branch `master`:
  - First run created protection (was unprotected HTTP 404)
  - Second run idempotent; readback `contexts=[ci-gate]`
  - `-Verify` exit 0

## Verification

```text
pytest tests/ci/test_ci_gate.py -q
# → pass

pytest tests/ci/test_branch_protection.py tests/ci/test_release_gate.py -q
# → pass

python scripts/ci/validate-workflow.py
# → [OK] workflow policy valid (includes ci-gate)

powershell -File scripts/ci/configure-branch-protection.ps1 -Repository adlink8/novel-mind
# → PUT + readback contexts=[ci-gate]  exit 0

powershell -File scripts/ci/configure-branch-protection.ps1 -Repository adlink8/novel-mind -Verify
# → readback contexts exactly ["ci-gate"]  exit 0

python scripts/ci/verify-release-gate.py --repository adlink8/novel-mind
# → PASS (after SUMMARY present)

pytest tests/ci -q
# → 85+ passed (full CI contract suite)
```

## Files Changed (06-07 scope)

| Path | Role |
|------|------|
| `.github/workflows/ci.yml` | Add aggregate job `ci-gate` only |
| `scripts/ci/ci-gate.py` | Fail-closed producer aggregate |
| `scripts/ci/configure-branch-protection.ps1` | Idempotent gh api protection + readback |
| `scripts/ci/verify-release-gate.py` | Phase 06 final release verifier |
| `tests/ci/test_ci_gate.py` | Aggregate matrix fixtures |
| `tests/ci/test_branch_protection.py` | Protection contract + context exactness |
| `tests/ci/test_release_gate.py` | Release evidence + remote inject cases |

## Live branch protection result

| Item | Value |
|------|-------|
| Repository | `adlink8/novel-mind` |
| Branch | `master` (default) |
| Required contexts (readback) | `["ci-gate"]` |
| Status | **configured + verified** (not blocked) |
| Auth | `gh` as `adlink8` with `repo` scope — Administration write succeeded |

## Deviations / notes

1. **Artifact manifests in live CI:** Producers from 06-06 do not yet emit unified `manifest.json` sidecars. The gate validates job `needs.*.result` always; artifact hash/schema/staleness checks activate when manifests are supplied (`--artifacts-dir` / `require_artifacts`). Unit fixtures cover those paths fail-closed.
2. **Alert job excluded from `needs`:** Alert is a failure side-effect job (D-18), not a green-path producer; including it would create awkward skip/failure coupling with nightly.
3. **PowerShell + gh stderr:** Native `gh` HTTP error lines on stderr are captured with `$ErrorActionPreference = Continue` so unprotected-branch `404` is not a terminating error.

## Out of Scope (confirmed)

- No re-implementation of 06-06 producer jobs, security scans, or alert logic
- No quality score re-interpretation inside `ci-gate`

## Phase 06 closeout

All seven plans (06-01..06-07) have SUMMARY evidence; unified CI has single required context `ci-gate`; remote branch protection readback matches. Phase 06 release gate: **PASS** when verifier is run with live readback.
