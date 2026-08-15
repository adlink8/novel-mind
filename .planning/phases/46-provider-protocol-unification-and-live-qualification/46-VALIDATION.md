---
phase: 46
slug: provider-protocol-unification-and-live-qualification
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-08-12
---

# Phase 46 - Validation Strategy

## Test Infrastructure

| Property | Value |
|---|---|
| **Framework** | pytest + Vitest + React Testing Library + Electron Playwright + Alembic |
| **Quick run command** | targeted provider registry/resolver contracts |
| **Full suite command** | backend full pytest/Ruff + frontend tests/type/lint + Agent tests/type + Electron provider settings spec |
| **Estimated runtime** | quick <120s; full local <20m; live provider matrix depends on authorized services |

## Sampling Rate

- TDD vertical slice: write one red provider/resolver/accounting test, implement only enough to
  turn it green, then refactor under the green suite.
- After each task: run its exact `<verify>` command.
- After each wave: run all tests through that wave twice for URL/owner/secret boundaries.
- Before Phase verdict: run migration/head, five-row evidence verifier, browser/Electron and
  tamper/missing-evidence negatives.
- Real provider retries are not a sampling mechanism; they follow the bounded authorization in
  46-03 and cannot be repeated to manufacture green.

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---|---:|---:|---|---|---|---|---|---|---|
| 46-01-01 | 01 | 0 | REQ-PROVIDER-01 | T-46-01-* | five native contracts fail red first | unit/contract | provider catalog + models + routing tests | ❌ W0 | pending |
| 46-01-02 | 01 | 0 | REQ-PROVIDER-01 | T-46-01-01..04 | bounded typed registry | unit/security | `test_provider_catalog.py` | ❌ W0 | pending |
| 46-01-03 | 01 | 0 | REQ-PROVIDER-01 | T-46-01-* | one CRUD/discovery/invoke validator | contract | models + AIService routing | ✅ partial foundation | pending |
| 46-01-04 | 01 | 0 | REQ-PROVIDER-01 | T-46-01-03 | backend-derived UI truth | component/type | models section + API tests | ✅ partial foundation | pending |
| 46-01-05 | 01 | 0 | REQ-PROVIDER-01 | T-46-01-* | repeated adversarial matrix | regression | plan full command | ❌ W0 | pending |
| 46-02-01 | 02 | 1 | REQ-PROVIDER-02 | T-46-02-* | red owner/fallback matrix | unit/contract | resolver + gateway + consumers | ❌ W0 | pending |
| 46-02-02 | 02 | 1 | REQ-PROVIDER-02 | T-46-02-01..03 | owner deployment deep module | unit/contract | resolver + gateway | ❌ W0 | pending |
| 46-02-03 | 02 | 1 | REQ-PROVIDER-02 | T-46-02-02/04 | consumers have no static fallback | integration | reader/knowledge/clue/derivative suites | ❌ W0 | pending |
| 46-02-04 | 02 | 1 | REQ-PROVIDER-02 | T-46-02-04 | static router loses authority | contract/source | settings/gateway + rg gate | ❌ W0 | pending |
| 46-02-05 | 02 | 1 | REQ-PROVIDER-02 | T-46-02-* | full backend owner regression | regression | backend full pytest/Ruff | ❌ W0 | pending |
| 46-03-01 | 03 | 2 | REQ-PROVIDER-03 | T-46-03-* | redacted bounded harness | contract | provider qualification tests | ❌ W0 | pending |
| 46-03-02 | 03 | 2 | REQ-PROVIDER-03 | T-46-03-01/03 | explicit credential/cost gate | manual gate | provider-specific authorization | N/A | pending |
| 46-03-03 | 03 | 2 | REQ-PROVIDER-03 | T-46-03-* | real five-row three-step matrix | live integration | qualification runner | ❌ W0 | blocked on credentials |
| 46-03-04 | 03 | 2 | REQ-PROVIDER-03 | T-46-03-01/02/04 | redacted evidence verdict | evidence | verify-evidence twice | ❌ W0 | pending |
| 46-04-01 | 04 | 3 | REQ-PROVIDER-04 | T-46-04-01..03 | red owner/cost tests | unit/API | usage tests | ❌ W0 | pending |
| 46-04-02 | 04 | 3 | REQ-PROVIDER-04 | T-46-04-02/03 | terminal lineage and unknown cost | migration/unit | Alembic + provider usage | ❌ W0 | pending |
| 46-04-03 | 04 | 3 | REQ-PROVIDER-04 | T-46-04-01/04 | owner API and honest UI | API/component | usage/models tests | ❌ W0 | pending |
| 46-04-04 | 04 | 3 | REQ-PROVIDER-04 | T-46-04-04 | packaged browser and evidence verdict | Electron/evidence | provider-settings spec + verifier | ❌ W0 | pending |
| 46-04-05 | 04 | 3 | REQ-PROVIDER-04 | T-46-04-* | tamper and full regression | regression/security | plan full command | ❌ W0 | pending |

## Wave 0 Requirements

- [ ] Add native one/multi-page provider fixtures and unsafe redirect/token-loop fixtures.
- [ ] Add two-owner model/usage fixtures including duplicate/missing defaults.
- [ ] Add secret canary and redacted qualification evidence schema/hash checker.
- [ ] Add known/unknown immutable price snapshot fixtures.
- [ ] Add Electron settings fixture server that is explicitly marked non-live.

## Manual-Only Verification

| Gate | Why manual/operator action is unavoidable | Automated proof around it |
|---|---|---|
| Supply cloud credentials/project context | credentials and paid-account authority cannot be invented or stored by the agent | presence-only preflight, bounded-call policy, secret scanner |
| Authorize possible provider cost | real completions may incur cost | exact max calls/tokens, no unbounded retry, usage capture |
| Make Ollama/custom service reachable | service ownership is external to repository | health/catalog/direct/Pi step statuses |

Manual action only unlocks real calls. It does not decide the verdict; the evidence checker does.

## Phase Verdict Rules

- `VERIFIED`: all four requirements pass and all five providers have real catalog, direct test
  and Pi outcomes required by the approved matrix.
- `PARTIAL`: local contracts/authority/accounting pass but at least one authorized provider has
  an honest unsupported/unavailable step.
- `BLOCKED`: credentials/authorization are absent, owner/security/evidence gates fail, or any
  required evidence is missing/tampered.
- Phase 22 0/3 and Phase 41 NO-GO are reported independently and never changed by this verdict.

## Validation Sign-Off

- [x] Every implementation task has an automated feedback command.
- [x] Security and owner boundaries have negative tests.
- [x] Real-provider proof is separated from mocks.
- [x] Missing credentials produce an honest blocked state.
- [x] No watch mode or unbounded live retry.

**Approval:** planning complete; execution not authorized
