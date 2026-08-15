---
phase: 43
slug: managed-local-runtime-and-data-lifecycle
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-08-09
---

# Phase 43 — Validation Strategy

## Test Infrastructure

| Property | Value |
|---|---|
| **Framework** | Vitest + Electron Playwright + Windows process fixtures |
| **Config file** | `desktop/package.json` |
| **Quick run command** | `cd desktop; npm test -- --run tests/runtime tests/data` |
| **Full suite command** | `cd desktop; npx playwright test tests/integration/runtime-lifecycle.spec.ts tests/integration/runtime-recovery.spec.ts` |
| **Estimated runtime** | quick <90s; full <15m |

## Sampling Rate

- After every task: targeted state/adapter/data/fault test.
- After waves 1 and 2: complete runtime/data suite.
- Before verification: packaged/development adapters, Windows process tree, migration and renderer recovery E2E.
- Max quick feedback latency: 90 seconds.

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---|---:|---:|---|---|---|---|---|---|---|
| 43-01-01 | 01 | 0 | REQ-DESK-03/07 | T-43-01-02 | legal lifecycle states/four methods | unit/type | adapter contract + typecheck | ❌ W0 | ⬜ pending |
| 43-01-02 | 01 | 0 | REQ-DESK-03/07 | T-43-01-01 | no packaged PATH/Docker fallback | contract | adapter contract | ❌ W0 | ⬜ pending |
| 43-01-03 | 01 | 0 | REQ-DESK-03/07 | T-43-01-* | idempotent failure/shutdown | unit/static | contract + authority scan | ❌ W0 | ⬜ pending |
| 43-02-01 | 02 | 1 | REQ-DESK-03/07 | T-43-02-03 | dependency-aware readiness | unit | process graph test | ❌ W0 | ⬜ pending |
| 43-02-02 | 02 | 1 | REQ-DESK-03/07 | T-43-02-01/02 | owned tree/redacted logs | Windows integration | process-tree test | ❌ W0 | ⬜ pending |
| 43-02-03 | 02 | 1 | REQ-DESK-03/07 | T-43-02-* | fault-bounded cleanup | fault injection | graph + tree tests | ❌ W0 | ⬜ pending |
| 43-03-01 | 03 | 1 | REQ-DESK-05/07 | T-43-03-02 | appData path containment | unit | app-data-layout test | ❌ W0 | ⬜ pending |
| 43-03-02 | 03 | 1 | REQ-DESK-05/07 | T-43-03-01/03 | backup-first atomic migration | fault integration | migration-recovery test | ❌ W0 | ⬜ pending |
| 43-03-03 | 03 | 1 | REQ-DESK-05/07 | T-43-03-* | denied/corrupt/interrupted safety | fault integration | data suite | ❌ W0 | ⬜ pending |
| 43-04-01 | 04 | 2 | REQ-DESK-03/05/07 | T-43-04-01/02 | no empty-success UI | component | RuntimeGate test | ❌ W0 | ⬜ pending |
| 43-04-02 | 04 | 2 | REQ-DESK-03/05/07 | T-43-04-* | real lifecycle recovery | E2E | runtime lifecycle/recovery specs | ❌ W0 | ⬜ pending |
| 43-04-03 | 04 | 2 | REQ-DESK-03/05/07 | T-43-04-* | repeated stable terminals | E2E/component | component + E2E twice | ❌ W0 | ⬜ pending |

## Wave 0 Requirements

- [ ] Create injectable process/state fixtures and shared adapter contract.
- [ ] Create Windows descendant/sentinel process fixture.
- [ ] Create non-empty migration/backup fixture with hash manifest.
- [ ] Create renderer RuntimeGate component fixture.

## Manual-Only Verifications

All required Phase 43 behavior is planned for automation; manual inspection is supplemental only.

## Validation Sign-Off

- [x] Every task has automated verification
- [x] Sampling continuity maintained
- [x] Fault and missing fixtures assigned to Wave 0
- [x] No watch mode
- [x] `nyquist_compliant: true`

**Approval:** pending execution

