---
phase: 45
slug: windows-packaging-migration-and-desktop-qualification
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-08-09
---

# Phase 45 — Validation Strategy

## Test Infrastructure

| Property | Value |
|---|---|
| **Framework** | Vitest + Electron Playwright + PowerShell clean-VM/evidence gates |
| **Config file** | `desktop/package.json`, `desktop/electron-builder.yml` |
| **Quick run command** | targeted package/update/security tests |
| **Full suite command** | `powershell -File desktop/tests/clean-vm/run-qualification.ps1 -Manifest desktop/tests/fixtures/qualification-manifest.json -RequireAll` |
| **Estimated runtime** | quick <2m; full clean-VM <60m |

## Sampling Rate

- After each implementation task: targeted package/update/security test.
- After each wave: build artifact plus all tests through that wave.
- Before verification: restored-snapshot clean-VM run, packaged security and evidence gate twice.
- Max quick feedback latency: 120 seconds; clean-VM is an explicit long gate.

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---|---:|---:|---|---|---|---|---|---|---|
| 45-01-01 | 01 | 0 | REQ-DESK-09/10 | T-45-01-01 | approved installer/dependencies | source gate | Phase 41 GO check | ❌ W0 | ⬜ pending |
| 45-01-02 | 01 | 0 | REQ-DESK-09/10 | T-45-01-01 | self-contained deterministic artifact | package | build verify + layout test | ❌ W0 | ⬜ pending |
| 45-01-03 | 01 | 0 | REQ-DESK-09 | T-45-01-02/03 | single instance/no console/orphan | Windows integration | process-behavior test | ❌ W0 | ⬜ pending |
| 45-01-04 | 01 | 0 | REQ-DESK-09/10 | T-45-01-* | repeat package/process proof | package | build + package tests | ❌ W0 | ⬜ pending |
| 45-02-01 | 02 | 1 | REQ-DESK-09/10 | T-45-02-01/03 | real-data upgrade preservation | E2E | upgrade-preservation spec | ❌ W0 | ⬜ pending |
| 45-02-02 | 02 | 1 | REQ-DESK-09/10 | T-45-02-01/02 | rollback/uninstall preserve | E2E | recovery + uninstall specs | ❌ W0 | ⬜ pending |
| 45-02-03 | 02 | 1 | REQ-DESK-09/10 | T-45-02-* | repeat/hash policy proof | E2E | complete update suite | ❌ W0 | ⬜ pending |
| 45-03-01 | 03 | 2 | REQ-DESK-10 | T-45-03-01 | authorized clean VM identity | human/source gate | manifest existence/hash | ❌ W0 | ⬜ pending |
| 45-03-02 | 03 | 2 | REQ-DESK-09/10 | T-45-03-* | all clean-VM flows/evidence | clean VM | qualification script | ❌ W0 | ⬜ pending |
| 45-03-03 | 03 | 2 | REQ-DESK-09/10 | T-45-03-* | restored-snapshot RequireAll | clean VM | qualification `-RequireAll` | ❌ W0 | ⬜ pending |
| 45-04-01 | 04 | 3 | REQ-DESK-10 | T-45-04-01/03 | packaged security/SBOM | security | release security + SBOM | ❌ W0 | ⬜ pending |
| 45-04-02 | 04 | 3 | REQ-DESK-09/10 | T-45-04-01/02 | evidence-derived verdict | evidence gate | verify-release-evidence | ❌ W0 | ⬜ pending |
| 45-04-03 | 04 | 3 | REQ-DESK-09/10 | T-45-04-* | twice + human honest closeout | evidence/human | verifier twice | ❌ W0 | ⬜ pending |

## Wave 0 Requirements

- [ ] Approve final installer dependencies/config after Phase 41 GO.
- [ ] Create package layout/process fixtures and prior-version data fixture.
- [ ] Provide/authorize clean Windows VM snapshot; paid resource requires explicit approval.
- [ ] Create qualification manifest with artifact/VM/fixture hashes.
- [ ] Create packaged security, SBOM and evidence-gate scripts.

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|---|---|---|---|
| Approve installer dependency set | REQ-DESK-09 | New production dependency | Review exact versions/license/artifact target |
| Provide/authorize clean VM | REQ-DESK-10 | External/local VM ownership and possible cost | Supply snapshot or explicitly authorize external use |
| Final closeout review | REQ-DESK-09/10 | Publication/signing/external gate truth | Review unsigned/signing, Phase 22 and all blockers before acceptance |

## Validation Sign-Off

- [x] Every task has automated verify or explicit human gate
- [x] No 3-task sampling gap
- [x] Missing fixtures and external gates assigned to Wave 0
- [x] No watch-mode flags
- [x] `nyquist_compliant: true`

**Approval:** pending execution

