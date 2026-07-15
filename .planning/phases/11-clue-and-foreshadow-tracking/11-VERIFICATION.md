---
phase: 11-clue-and-foreshadow-tracking
verified: 2026-07-15T10:55:00Z
status: partial
score: 15/15 must-have truths verified (product); adversarial suite residual 1 failed static scan
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
  gaps_closed: []
  gaps_remaining:
    - "tests/adversarial/test_clue_false_positives.py::test_service_files_contain_no_asyncsession_lifecycle_writes fails — package-wide session.add scan flags intentional 11-03 persistence modules (budget/lifecycle/overrides/versions/worker); pure recall/judge/gate modules remain clean"
  regressions:
    - "CI clue job runs the failing adversarial file; full suite is not green until the scanner is scoped to pure modules"
---

# Phase 11: Clue and Foreshadow Tracking — Verification Report

**Phase Goal:** 以证据、版本和人工可控的五状态生命周期，发现、追踪、核验小说中的线索、伏笔及其回收。  
**Verified:** 2026-07-15T10:55:00Z  
**Status:** partial  
**Re-verification:** No — first independent verification after 11-01..11-05

## Goal Achievement

Phase 11 vertical slice is implement-complete in code: clue-owned PostgreSQL authority, deterministic candidate recall + bounded LLM judgment + gates, durable worker/version/override path, spoiler-safe owner API, `/analysis` clue workspace (no top-level `/clues`), frozen offline qualification, and fail-closed release-gate contracts.

Independent re-run results this session (PostgreSQL CI URL on **5433**):

| Suite | Result |
|---|---|
| Backend unit `tests/unit/clues` | **58 passed** (part of combined run) |
| Backend integration `tests/integration/clues` (PG 5433) | **35 passed** (incl. real_qualification 7) |
| Adversarial clue suites | **10 passed, 1 failed** |
| Combined targeted backend | **103 passed, 1 failed** (~22s) |
| CI `tests/ci/test_clue_release_gate.py` | **9 passed** |
| Offline `run_clue_qualification.py --offline` | **status=qualified**, critical all 0, paid_off precision 1.0, macro F1 1.0 |
| Frontend Vitest `clue` | **15 passed** (2 files) |
| Real Playwright `clue-real.spec.ts` | **Not re-run** this session; artifact present; 11-05 SUMMARY reports 2/2 (desktop+390) on 5433 |
| Host Postgres | **5433 up**, **5432 down** (matches integration default / CI lock) |
| Alembic | head + current **`11cluetrack01`** on novelmind_ci |

### Special boundary checks (user-required)

| Check | Result | Evidence |
|---|---|---|
| Chat never a clue fact source | ✓ | `sources.reject_freeform_chat_as_evidence` → `chat_freeform_forbidden` / `phase10_chat_is_not_clue_fact_source`; link schemas forbid chat/similarity evidence fields; adversarial `test_chat_similarity_cannot_accept_active_or_paid_off`; offline critical `chat_as_fact=0` |
| Phase 09 outage → `source_unavailable` | ✓ | `RelationshipSourceStatus` + null/outage readers in `services/clues/sources.py`; gates reject `source_unavailable` as published relation; adversarial `test_relationship_source_unavailable_is_explicit_not_empty_success` |
| Five-state lifecycle | ✓ | `ClueLifecycleState` + `LEGAL_TRANSITIONS` + `replay_lifecycle` (no mutable current-status column); unit lifecycle 8 + PG persistence append-only/paid_off order |
| Spoiler / full-book | ✓ | Query uses reading progress cutoff then derives state/counts/filters/links/chains; full-book only `timeline_full_book` (no clue-specific preference); integration spoiler API + adversarial paid_off hide until cutoff |
| UI on `/analysis`, not `/clues` | ✓ | `frontend/src/app/analysis/page.tsx` workspace tab「线索与伏笔」; **no** `frontend/src/app/clues`; page tests assert no top-level /clues link; components under `frontend/src/components/clues/` |

### Observable Truths (plan must-haves)

| # | Truth | Plan | Status | Evidence |
|---:|---|---|---|---|
| 1 | Clue lifecycle history is append-only and only legal candidate/active/reinforced/paid_off/dismissed transitions persist | 11-01 | ✓ VERIFIED | `schemas/clue.py` LEGAL_TRANSITIONS; unit `test_lifecycle.py` (8); PG `test_persistence.py` (12) append-only triggers |
| 2 | active, reinforced and paid_off rows cannot exist without role-correct evidence; paid_off requires earlier cue and later payoff | 11-01 | ✓ VERIFIED | App validators + DB paid_off order guard; false-positive adversarial functional cases; offline critical false_paid_off=0 |
| 3 | Machine versions and human overrides are separate, so reanalysis cannot overwrite a human decision | 11-01 | ✓ VERIFIED | Separate ORM tables; override supersession INSERT-only; `test_override_reanalysis.py` (2) |
| 4 | Deterministic scripts build every cross-chapter candidate and allowed-evidence package before an LLM is called | 11-02 | ✓ VERIFIED | `ClueCandidateRecallService` + evidence packages; unit `test_candidates.py` (6) |
| 5 | LLM output is semantic judgment only and cannot directly create lifecycle state, links, versions or writes | 11-02 | ✓ VERIFIED | `llm_judge.py` pure parse/validate; pure modules (`candidates/evidence/gates/llm_judge/sources/query/eval`) have **no** `session.add` / `ClueLifecycleEvent(` |
| 6 | Similarity, chat and unresolved relationship observations remain recall signals and produce zero accepted states by themselves | 11-02 | ✓ VERIFIED | GateService pure decisions; adversarial false-positives + chat/source tests; offline critical false_active/false_paid_off=0 |
| 7 | A clue run resumes from durable checkpoints, reserves budget before each model call and never moves active on failure | 11-03 | ✓ VERIFIED | `worker.py` + `budget.py`; integration `test_worker_versions.py` (3) |
| 8 | Lifecycle writes and human actions append history; reanalysis creates a distinct comparable version and preserves overrides | 11-03 | ✓ VERIFIED | lifecycle/overrides services + PG override reanalysis tests |
| 9 | Every owner-scoped API response filters by reading progress before deriving visible state, counts, filters, links or payoff chains | 11-03 | ✓ VERIFIED | `query.py` visible-set-first; unit projection (3) + integration spoiler API (3) |
| 10 | Users inspect clues inside `/analysis` with the existing novel selector and full-book confirmation | 11-04 | ✓ VERIFIED | analysis page tab + shared full-book; Vitest page + workspace |
| 11 | The clue band, keyboard list, filters and evidence panel use the exact same spoiler-safe visible set | 11-04 | ✓ VERIFIED | `clue-workspace.tsx` + band/controls/evidence; `clue-workspace.test.tsx` (8) |
| 12 | Users can confirm, reject, annotate and adjust links without exposing summary middleware or adding a top-level route | 11-04 | ✓ VERIFIED | Human actions in workspace; no `/clues` app route; page tests |
| 13 | Frozen fiction qualification separates recall quality from lifecycle publication quality and has zero critical false active/paid_off | 11-05 | ✓ VERIFIED | Fixture 24 cases + adversarial_cases; offline report critical all 0; unit `test_eval.py` (9) |
| 14 | Real API and desktop/mobile browser journeys prove spoiler-safe clue viewing and all protected human actions | 11-05 | ✓ VERIFIED (artifact + prior) | `frontend/e2e/clue-real.spec.ts` present; 11-05: 2/2 on 5433; **not re-executed** this session |
| 15 | Release passes only from fresh PostgreSQL authority, measured metrics and internally executed fixed commands; blocked dependencies fail closed | 11-05 | ✓ VERIFIED | `run_clue_qualification.py` verify_release; `tests/ci/test_clue_release_gate.py` **9 passed**; PG `test_real_qualification.py` **7 passed** |

**Score:** 15/15 product must-have truths verified; **overall PARTIAL** because the full adversarial suite is not green (see residual).

## Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `backend/app/schemas/clue.py` | Strict lifecycle/evidence/link/override contracts | ✓ VERIFIED | Present; five states + LEGAL_TRANSITIONS |
| `backend/app/models/clue.py` | Clue-owned authority ORM | ✓ VERIFIED | Present |
| `backend/migrations/versions/11_clue_tracking.py` | Alembic `11cluetrack01` | ✓ VERIFIED | head after `12readerchat01`; current on 5433 |
| `backend/app/services/clues/*` | candidates, evidence, gates, llm, lifecycle, worker, query, sources, budget, overrides, versions, eval | ✓ VERIFIED | Pure vs persistence split observed |
| `backend/app/api` clue routes | Owner-scoped HTTP | ✓ VERIFIED | OpenAPI clue paths registered (import via app) |
| `backend/evals/clue_fiction.v1.json` | Frozen 24-case fiction fixture | ✓ VERIFIED | 24 cases; domain fiction |
| `backend/scripts/run_clue_qualification.py` | Offline/prod/e2e/release CLI | ✓ VERIFIED | offline → qualified this session |
| `tests/ci/test_clue_release_gate.py` | Independent release authority | ✓ VERIFIED | 9 passed |
| `frontend/src/lib/clue-api.ts` | Dedicated clue client | ✓ VERIFIED | Present + 7 contract tests |
| `frontend/src/components/clues/*` | Workspace/band/controls/evidence | ✓ VERIFIED | Present |
| `frontend/src/app/analysis/page.tsx` | Tab host, no top-level /clues | ✓ VERIFIED | Present |
| `frontend/e2e/clue-real.spec.ts` | Dual viewport real stack | ✓ VERIFIED (artifact) | Present; not re-run |
| `.github/workflows/ci.yml` | Clue qualification job | ✓ VERIFIED | Wires real_qual + adversarial + offline + scope-scan |

## Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| Lifecycle current state | event log | `replay_lifecycle` only | ✓ WIRED | No authoritative mutable current_status |
| paid_off | cue+payoff coords | app + PG trigger | ✓ WIRED | Strict later order |
| Relationship signals | Phase 09 reader | `sources.py` protocol | ✓ WIRED | outage → `source_unavailable` |
| Chat freeform | clue evidence | hard reject | ✓ WIRED | never fact source |
| Spoiler cutoff | reading_progress + `timeline_full_book` | query projection | ✓ WIRED | Phase 08 preference only |
| Human overrides | reanalysis versions | append supersession + needs_relink | ✓ WIRED | integration tests |
| UI | server envelope | `clue-api` + ClueWorkspace | ✓ WIRED | `/analysis` tab only |
| Release | commands + fresh PG | CLI observer | ✓ WIRED | contract tests + real_qual |

## Data-Flow Trace

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| Candidate package | evidence IDs / hashes | hierarchy + timeline lineage | Yes | ✓ FLOWING |
| Gate decision | accepted / reason codes | pure GateService | Yes | ✓ FLOWING |
| Lifecycle event | append-only row | worker/human after gates | Yes (PG tests) | ✓ FLOWING |
| Visible envelope | state/counts/filters/links/chains | cutoff-first query | Yes | ✓ FLOWING |
| Offline qualification | precision/F1/critical | frozen fixture + eval | Yes this session | ✓ FLOWING |
| Real browser authority | spoiler + 4 actions | Next+API+PG | Prior 11-05 only | ⚠ NOT RE-RUN |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Backend clue unit/integration/adversarial | `pytest tests/unit/clues tests/integration/clues tests/adversarial/test_clue_false_positives.py tests/adversarial/test_clue_spoilers_and_versions.py -q` (DB 5433) | **103 passed, 1 failed** | ⚠ PARTIAL |
| Release gate | `pytest tests/ci/test_clue_release_gate.py -q` (repo root) | **9 passed** | ✓ PASS |
| Offline qualification | `python scripts/run_clue_qualification.py --offline --fixture evals/clue_fiction.v1.json` | **qualified**, critical=0 | ✓ PASS |
| Frontend clue Vitest | `npm test -- --run clue` | **15 passed** | ✓ PASS |
| Alembic head/current | `alembic heads` / `current` on 5433 | **11cluetrack01** | ✓ PASS |
| Postgres 5433 | `Test-NetConnection 127.0.0.1:5433` | True | ✓ PASS |
| Postgres 5432 | `Test-NetConnection 127.0.0.1:5432` | False | info (not used by clue CI default) |
| Pure-module write scan | candidates/evidence/gates/llm_judge/sources/query/eval | **all clean** | ✓ PASS |
| Package-wide write scan (adversarial) | all `services/clues/*.py` | fails on budget/lifecycle/overrides/versions/worker | ✗ FAIL (stale scope) |

### Residual detail: adversarial static scan

`test_service_files_contain_no_asyncsession_lifecycle_writes` treats **any** `session.add` / `ClueLifecycleEvent(` under `app/services/clues/` as an offense. That matched 11-02’s pure-service intent, but 11-03 intentionally introduced persistence writers:

- offenders: `budget.py`, `lifecycle.py` (+ ctor), `overrides.py`, `versions.py`, `worker.py`
- non-offenders (pure path): `candidates.py`, `evidence.py`, `gates.py`, `llm_judge.py`, `sources.py`, `query.py`, `eval.py`, `__init__.py`

**Product boundary still holds** (LLM/recall/gates cannot write). **CI residual** remains because `.github/workflows/ci.yml` runs `tests/adversarial/test_clue_false_positives.py` in the clue job. Fix: narrow the scan to pure modules (or allowlist persistence modules). Out of scope for this docs-only verification commit.

## Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|---|---|---|---|---|
| REQ-CLUE-01 | 02, 05 | Script candidates + bounded LLM + gates | ✓ SATISFIED | unit candidates/gates/llm + offline metrics |
| REQ-CLUE-02 | 01, 03, 05 | Append-only five-state lifecycle | ✓ SATISFIED | lifecycle pure + PG persistence |
| REQ-CLUE-03 | 01–03, 05 | Evidence-only links; chat not fact; source_unavailable | ✓ SATISFIED | sources + adversarial spoilers |
| REQ-CLUE-04 | 03–05 | Spoiler before derived fields; timeline_full_book only | ✓ SATISFIED | query + spoiler API |
| REQ-CLUE-05 | 01, 03–05 | Protected overrides + comparable reanalysis | ✓ SATISFIED | overrides + reanalysis PG |
| REQ-CLUE-06 | 04, 05 | `/analysis` clue surface | ✓ SATISFIED | UI + Vitest; e2e prior |
| REQ-CLUE-07 | 05 | Frozen/adversarial/API/browser/release | ⚠ PARTIAL | offline + release + PG real_qual green; **one adversarial static test fails**; Playwright not re-run this session |

## Anti-Patterns

| Pattern | Status |
|---|---|
| Chat as lifecycle evidence | Absent (hard reject + critical counter) |
| Similarity alone → active/paid_off | Absent (gates + adversarial) |
| Phase 09 outage as empty-success | Absent (`source_unavailable`) |
| Mutable current_status authority | Absent (`replay_lifecycle`) |
| Clue-specific full-book preference | Absent (reuses `timeline_full_book`) |
| Top-level `/clues` route | Absent |
| History / writing / graph mutation in Phase 11 | Absent from clue services (by design) |

## Overall Verdict

**PARTIAL** — product must-haves for REQ-CLUE-01..06 and nearly all of REQ-CLUE-07 are independently evidenced on PostgreSQL 5433, offline qualification, release contracts, and frontend unit tests. One adversarial package-wide static scan fails against intentional 11-03 persistence writers and will fail the CI clue job until scoped. Real dual-viewport Playwright was not re-executed in this session (prior 11-05 evidence retained).

## Recommended Close Actions

1. Narrow `test_service_files_contain_no_asyncsession_lifecycle_writes` to pure modules (or allowlist `budget/lifecycle/overrides/versions/worker`).
2. Re-run `npm run test:e2e -- clue-real.spec.ts` with stack on 5433 for independent browser authority.
3. After (1) green, promote Phase 11 status to **VERIFIED** / ship-ready for v0.7 close-out.
