---
phase: 44
slug: desktop-transport-credentials-and-offline-behavior
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-08-09
---

# Phase 44 — Validation Strategy

## Test Infrastructure

| Property | Value |
|---|---|
| **Framework** | Vitest + pytest + Electron Playwright |
| **Config file** | existing frontend/backend/agent configs plus `desktop/package.json` |
| **Quick run command** | targeted resolver/credential/auth unit tests |
| **Full suite command** | `cd desktop; npx playwright test tests/integration/sse-recovery.spec.ts tests/integration/offline-workflows.spec.ts` |
| **Estimated runtime** | quick <90s; full <12m |

## Sampling Rate

- After each task: run its exact frontend/desktop/backend/agent test.
- After each wave: run all tests through that wave.
- Before verification: dynamic endpoint, safeStorage/auth, SSE and network-disabled E2E.
- Max quick feedback latency: 90 seconds.

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---|---:|---:|---|---|---|---|---|---|---|
| 44-01-01 | 01 | 0 | REQ-DESK-06 | T-44-01-01/02 | loopback bounded bootstrap | unit | desktop bootstrap test | ❌ W0 | ⬜ pending |
| 44-01-02 | 01 | 0 | REQ-DESK-06 | T-44-01-01/02 | one resolver/no public runtime env | unit/type | resolver test + typecheck | ❌ W0 | ⬜ pending |
| 44-01-03 | 01 | 0 | REQ-DESK-06 | T-44-01-* | restart/malformed/bundle scan | build/unit | bootstrap + resolver + build | ❌ W0 | ⬜ pending |
| 44-02-01 | 02 | 1 | REQ-DESK-06 | T-44-02-01 | async safeStorage/no plaintext | unit | credential-store test | ❌ W0 | ⬜ pending |
| 44-02-02 | 02 | 1 | REQ-DESK-06 | T-44-02-02/03 | audience/expiry/session auth | security | pytest + agent Vitest | ❌ W0 | ⬜ pending |
| 44-02-03 | 02 | 1 | REQ-DESK-06 | T-44-02-* | rotation/corruption fail closed | security | all auth suites twice | ❌ W0 | ⬜ pending |
| 44-03-01 | 03 | 2 | REQ-DESK-06/08 | T-44-03-01/02 | SSE terminal/replay safety | E2E | sse-recovery spec | ❌ W0 | ⬜ pending |
| 44-03-02 | 03 | 2 | REQ-DESK-08 | T-44-03-03 | local offline/provider blocked | unit/E2E | capability + offline spec | ❌ W0 | ⬜ pending |
| 44-03-03 | 03 | 2 | REQ-DESK-06/08 | T-44-03-* | repeated honest terminals | E2E | SSE + offline twice | ❌ W0 | ⬜ pending |

## Wave 0 Requirements

- [ ] Add bootstrap/resolver fixtures with rotating ports and sessions.
- [ ] Add safeStorage mock/provider and plaintext secret-scan fixture.
- [ ] Add backend/Agent local-auth middleware fixtures.
- [ ] Add network-disabled and SSE replay/cancellation fixtures.

## Manual-Only Verifications

All required Phase 44 behaviors have automated paths; manual provider account setup is not accepted as sole evidence.

## Validation Sign-Off

- [x] Every task has automated verification
- [x] Sampling continuity maintained
- [x] Missing fixtures assigned to Wave 0
- [x] No watch mode
- [x] `nyquist_compliant: true`

**Approval:** pending execution

