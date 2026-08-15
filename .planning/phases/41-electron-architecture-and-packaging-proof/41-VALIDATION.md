---
phase: 41
slug: electron-architecture-and-packaging-proof
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-08-09
---

# Phase 41 — Validation Strategy

> Per-phase validation contract for the fail-closed Electron/packaging proof.

## Test Infrastructure

| Property | Value |
|---|---|
| **Framework** | Vitest + Playwright + PowerShell proof scripts |
| **Config file** | `desktop/proof/package.json` — Wave 0 creates after dependency approval |
| **Quick run command** | `cd desktop/proof; npm test -- --run` |
| **Full suite command** | `powershell -File desktop/proof/scripts/verify-bundled-runtime.ps1 -Manifest desktop/proof/runtime-manifest.json` |
| **Estimated runtime** | quick <60s; full clean-fixture run <20m |

## Sampling Rate

- **After every task commit:** Run the task's targeted automated command.
- **After every plan wave:** Run all tests created through that wave.
- **Before `$gsd-verify-work`:** Route parity, bundled-runtime negatives and decision derivation must be green.
- **Max feedback latency:** 60 seconds for unit/contract tasks; clean fixture is an explicit long gate.

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---|---:|---:|---|---|---|---|---|---|---|
| 41-01-01 | 01 | 0 | REQ-DESK-04 | T-41-01-01 | dependency approval before install | source gate | `Select-String desktop/proof/README.md Electron` | ❌ W0 | ⬜ pending |
| 41-01-02 | 01 | 0 | REQ-DESK-04 | T-41-01-01/02 | unsafe topology rejects before spawn | unit | `npm test -- --run tests/topology.test.ts` | ❌ W0 | ⬜ pending |
| 41-01-03 | 01 | 0 | REQ-DESK-04 | T-41-01-01/02 | repeated contract proof | unit/type | `npm run typecheck; npm test -- --run tests/topology.test.ts` | ❌ W0 | ⬜ pending |
| 41-02-01 | 02 | 1 | REQ-DESK-04 | T-41-02-01/02 | complete standalone assets and owned shutdown | integration | `build-next-standalone.ps1 -VerifyOnly` | ❌ W0 | ⬜ pending |
| 41-02-02 | 02 | 1 | REQ-DESK-04 | T-41-02-01 | exactly 13 routes render | E2E | `npx playwright test tests/route-parity.spec.ts` | ❌ W0 | ⬜ pending |
| 41-02-03 | 02 | 1 | REQ-DESK-04 | T-41-02-01/02 | reproducible build/parity | E2E | same build + parity commands | ❌ W0 | ⬜ pending |
| 41-03-01 | 03 | 2 | REQ-DESK-04 | T-41-03-01 | no Docker/user runtime | clean fixture | `verify-bundled-runtime.ps1` | ❌ W0 | ⬜ pending |
| 41-03-02 | 03 | 2 | REQ-DESK-04 | T-41-03-01/02 | unknown evidence yields NO-GO | unit | `npm test -- --run tests/runtime-feasibility.test.ts` | ❌ W0 | ⬜ pending |
| 41-03-03 | 03 | 2 | REQ-DESK-04 | T-41-03-01/02 | negative and clean verdict proof | integration | full proof + Phase 22 diff guard | ❌ W0 | ⬜ pending |

## Wave 0 Requirements

- [ ] Approve exact Electron/packaging proof dependencies before install.
- [ ] Create `desktop/proof` package and topology fixtures.
- [ ] Freeze the 13-route inventory from current `frontend/src/app`.
- [ ] Prepare a no-Docker/PATH-cleared Windows proof fixture.

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|---|---|---|---|
| Approve new production dependencies | REQ-DESK-04 | Policy requires user authorization | Review exact versions/licenses and explicitly approve before install |
| Accept Phase 41 GO/NO-GO | REQ-DESK-04 | Architectural gate changes downstream eligibility | Review evidence hashes and failed prerequisites; do not override unknown rows |

## Validation Sign-Off

- [x] All tasks have automated verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 lists all missing fixtures
- [x] No watch-mode flags
- [x] Quick feedback latency target <60s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending execution

