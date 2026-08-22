---
phase: 42
slug: secure-desktop-shell
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-08-09
---

# Phase 42 — Validation Strategy

## Test Infrastructure

| Property | Value |
|---|---|
| **Framework** | Vitest + Electron Playwright |
| **Config file** | `desktop/package.json` — created after approval |
| **Quick run command** | `cd desktop; npm test -- --run` |
| **Full suite command** | `cd desktop; npx playwright test tests/security tests/e2e` |
| **Estimated runtime** | quick <60s; full <10m |

## Sampling Rate

- After every task: targeted Vitest/Playwright command.
- After each wave: all shell/security tests created so far.
- Before verification: all 13 routes, critical workflows and privilege negatives.
- Max quick feedback latency: 60 seconds.

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---|---:|---:|---|---|---|---|---|---|---|
| 42-01-01 | 01 | 0 | REQ-DESK-01/02 | T-42-01-01 | GO/dependency gate | source gate | check `41-DECISION.md` GO | ❌ W0 | ⬜ pending |
| 42-01-02 | 01 | 0 | REQ-DESK-01/02 | T-42-01-01/02 | sandboxed four-capability bridge | smoke/type | `npm run typecheck; playwright shell-smoke` | ❌ W0 | ⬜ pending |
| 42-01-03 | 01 | 0 | REQ-DESK-01/02 | T-42-01-01/02 | browser/desktop safe compatibility | smoke/type | desktop + frontend typecheck | ❌ W0 | ⬜ pending |
| 42-02-01 | 02 | 1 | REQ-DESK-02 | T-42-02-02 | deny navigation/window/permissions | security E2E | `playwright tests/security/policy.spec.ts` | ❌ W0 | ⬜ pending |
| 42-02-02 | 02 | 1 | REQ-DESK-02 | T-42-02-01/03 | sender/schema validated IPC | unit | `npm test -- --run tests/security/ipc.spec.ts` | ❌ W0 | ⬜ pending |
| 42-02-03 | 02 | 1 | REQ-DESK-02 | T-42-02-* | no generic IPC/security warning | security | policy + IPC + `rg` scan | ❌ W0 | ⬜ pending |
| 42-03-01 | 03 | 2 | REQ-DESK-01/02 | T-42-03-01 | optional typed bridge only | type/static | frontend typecheck + Electron import scan | ❌ W0 | ⬜ pending |
| 42-03-02 | 03 | 2 | REQ-DESK-01/02 | T-42-03-01/02 | routes/workflows plus privilege negatives | E2E | `playwright tests/e2e` | ❌ W0 | ⬜ pending |
| 42-03-03 | 03 | 2 | REQ-DESK-01/02 | T-42-03-* | desktop twice/browser regression | E2E/unit | desktop E2E + frontend tests | ❌ W0 | ⬜ pending |

## Wave 0 Requirements

- [ ] Create approved `desktop` package and Electron Playwright harness.
- [ ] Reuse Phase 41 13-route inventory.
- [ ] Add test fixtures for hostile origin/frame/payload/renderer API attempts.

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|---|---|---|---|
| Approve production Electron dependencies | REQ-DESK-01/02 | New production dependency policy | Review exact versions after Phase 41 GO |

## Validation Sign-Off

- [x] Every task has an automated command
- [x] No 3-task sampling gap
- [x] Missing fixtures are Wave 0
- [x] No watch mode
- [x] `nyquist_compliant: true`

**Approval:** pending execution

