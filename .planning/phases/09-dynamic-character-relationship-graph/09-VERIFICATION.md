---
phase: 09-dynamic-character-relationship-graph
verified: 2026-07-15T09:15:00Z
status: passed
score: 21/21 must-have truths verified
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
  gaps_closed: []
  gaps_remaining: []
  regressions: []
---

# Phase 9: Dynamic Character Relationship Graph Verification Report

**Phase Goal:** 基于已证据门控的人物关系事实和时间线版本，生成可按叙事进度演化、默认防剧透的小说人物关系图。  
**Verified:** 2026-07-15T09:15:00Z  
**Status:** passed  
**Re-verification:** No — first independent verification after 09-01..09-05

## Goal Achievement

Phase 09 is achieved in the current codebase. PostgreSQL is the sole accepted-observation authority; an idempotent pipeline writes fiction-only five-type observations from Phase 04 accepted judgments; the graph API is owner/version/spoiler scoped with visible-set-first fold; `/analysis` hosts a Cytoscape workspace sharing Phase 08 timeline state; release/adversarial/performance gates fail closed. Independent re-runs of the Phase 09 backend suite (60) and frontend relationships Vitest suite (17) passed; scope scan reports `scope_clean=true`.

### Observable Truths (plan must-haves)

| # | Truth | Plan | Status | Evidence |
|---:|---|---|---|---|
| 1 | D-01/D-03: PostgreSQL stores immutable, append-only relationship observations bound to owner, novel, analysis version and accepted source judgment | 09-01 | ✓ VERIFIED | `backend/app/models/relationship.py` (`RelationshipObservation` + FKs); migration `11relobserve01`; `tests/integration/relationships/test_persistence.py` (13 passed in suite) |
| 2 | D-04: Legacy `character_relations` cannot satisfy Phase 09 observation contract | 09-01 | ✓ VERIFIED | Comment-only exclusion in `query.py`; adversarial/PG boundary tests reject legacy contamination; no service import of `CharacterRelation` |
| 3 | D-17/D-18: Character merge, relation type and interval corrections are append-only overrides with supersession and relink state | 09-01, 09-03 | ✓ VERIFIED | `CharacterIdentityOverride` / `RelationshipOverride` models + triggers; `services/relationships/overrides.py`; API/override integration tests |
| 4 | D-13/D-14: LLM performs bounded semantic judgment only; scripts own source, evidence, thresholds, state and writes | 09-02 | ✓ VERIFIED | `candidates.py` / `evidence.py` / `judgment.py` / `gates.py` / `worker.py`; unit pipeline tests assert package allowlists and non-write candidates |
| 5 | D-15/D-16: Threshold and state-machine outcomes are deterministic, versioned and auditable | 09-02 | ✓ VERIFIED | `AUTO_ACCEPT_THRESHOLD = 0.85` in `gates.py`; boundary tests at threshold bands; `policy_hash` in worker |
| 6 | D-05/D-07: Only fiction accepted judgments for five character relation types can become observations | 09-02 | ✓ VERIFIED | `RelationshipEdgeType` = ally/enemy/family/mentor/romantic; source query requires accepted/accepted fiction judgments; causes/precedes/same_entity never create edges |
| 7 | D-06: Every node, type, filter, count and evidence preview derives from the same visible accepted observation set | 09-03 | ✓ VERIFIED | `RelationshipGraphQueryService` fold → derive path; spoiler/API tests in `test_api.py` |
| 8 | D-09: Cutoff from persisted reading progress; missing/invalid → chapter one; no chapters → empty graph | 09-03 | ✓ VERIFIED | `query.py` cutoff policy; integration spoiler tests |
| 9 | D-10: Full-book disclosure reuses only Phase 08 `timeline_full_book` preference | 09-03 | ✓ VERIFIED | `timeline_full_book` reads in `query.py`; no second graph preference field |
| 10 | D-11: Graph response owner/novel/version/spoiler scoped; active and running candidate never merge | 09-03 | ✓ VERIFIED | Version proof in query service; isolation tests in API suite |
| 11 | D-12: Client-selected version is re-proven server-side inside owner/novel | 09-03 | ✓ VERIFIED | Server version resolution before query/override; cross-owner 404 tests |
| 12 | D-02: Neo4j/projection is replayable from accepted PostgreSQL manifests and never authoritative | 09-03 | ✓ VERIFIED | `replay_accepted_observations` in `projection.py`; `test_projection.py` checksum + failure isolation |
| 13 | D-23/D-24: Downstream phases receive read-only filtered contract and accepted IDs/evidence only | 09-03, 09-05 | ✓ VERIFIED | `load_filtered_relationship_graph` + `list_accepted_observation_refs` on query service; scope scan `phase10/11_contract_present=true`, no chat/clue modules |
| 14 | D-08/D-21: `/analysis` relationship workspace with filters, zoom, evidence and keyboard-equivalent controls | 09-04 | ✓ VERIFIED | `relationship-workspace.tsx` + controls/graph/evidence; `relationships.test.tsx` (12) + contract tests (5) |
| 15 | D-19: Cytoscape.js is the relationship renderer (exact 3.34.0, built-in types) | 09-04 | ✓ VERIFIED | `frontend/package.json` pins `"cytoscape": "3.34.0"`; `relationship-graph.tsx` imports cytoscape |
| 16 | D-20: Existing ECharts timeline remains; shared only source/version/narrative position | 09-04 | ✓ VERIFIED | `page.tsx` still mounts `TimelineChart`; `timeline-chart.tsx` ECharts path preserved |
| 17 | D-22: Normal / large / filters_required degrade deterministically without partial-truth display | 09-04, 09-05 | ✓ VERIFIED | Graph component skips Cytoscape on `filters_required`; API empties elements over hard caps; performance tests assert tiers |
| 18 | All REQ-REL-01..06 critical gates pass on PostgreSQL and browser-contract paths before release | 09-05 | ✓ VERIFIED | Backend 60 passed re-run; frontend relationships 17 passed; release_gate tests in suite; CI wires relationship qualification |
| 19 | D-22 performance/degradation and D-02 projection replay have measured, fail-closed evidence | 09-05 | ✓ VERIFIED | `test_performance.py` (2) + projection (3) + release_gate (6) passed in independent suite |
| 20 | D-23/D-24 contracts present while session/chat/clue artifacts remain absent | 09-05 | ✓ VERIFIED | `--scope-scan` → `scope_clean=true`, `forbidden_hits=[]` |
| 21 | Release evidence is independently observed, version-bound and command-backed | 09-05 | ✓ VERIFIED | `run_relationship_qualification.py` (`blocked_release`, `session_factory`); `test_release_gate.py` fail-closed cases passed |

**Score:** 21/21 must-have truths verified

## Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `backend/app/models/relationship.py` | 8 authority ORM classes | ✓ VERIFIED | Present; import OK; FKs to analysis_versions + knowledge_relation_judgments |
| `backend/app/schemas/relationship.py` | Strict enums + graph envelopes | ✓ VERIFIED | `RelationshipGraphEnvelope`, five edge types only |
| `backend/migrations/versions/11_relationship_observations.py` | Alembic `11relobserve01` + append-only triggers | ✓ VERIFIED | `revision = "11relobserve01"`, `down_revision = "10analysistime01"` |
| `backend/app/services/relationships/gates.py` | Deterministic gates + thresholds | ✓ VERIFIED | `AUTO_ACCEPT_THRESHOLD = 0.85` |
| `backend/app/services/relationships/worker.py` | Sole accepted-observation writer | ✓ VERIFIED | `class RelationshipObservationWorker` |
| `backend/prompts/relationship_semantic_judge.v1.txt` | Frozen fiction semantic prompt | ✓ VERIFIED | Present |
| `backend/app/services/relationships/query.py` | Version proof, fold, spoiler projection | ✓ VERIFIED | `RelationshipGraphQueryService` + Phase 10/11 readers |
| `backend/app/services/relationships/overrides.py` | Append-only overrides | ✓ VERIFIED | Present |
| `backend/app/services/relationships/projection.py` | Replay boundary | ✓ VERIFIED | `replay_accepted_observations` |
| `backend/app/api/relationships.py` | Owner-scoped graph/evidence/override API | ✓ VERIFIED | Uses `require_owned_novel`; mounted in `main.py` at `/api/relationships` |
| `frontend/src/components/relationships/relationship-graph.tsx` | Cytoscape lifecycle | ✓ VERIFIED | cytoscape import; filters_required guard |
| `frontend/src/components/relationships/relationship-workspace.tsx` | Typed graph fetch workspace | ✓ VERIFIED | `relationshipsApi.getGraph` |
| `frontend/src/components/relationships/relationship-evidence-panel.tsx` | Evidence/provenance panel | ✓ VERIFIED | Present |
| `frontend/src/app/analysis/page.tsx` | Shared workspace orchestration | ✓ VERIFIED | Timeline + relationships tabs |
| `backend/scripts/run_relationship_qualification.py` | Executable release CLI | ✓ VERIFIED | `blocked_release` paths; scope-scan mode |
| `backend/evals/relationship_fiction.v1.json` | Frozen fiction corpus | ✓ VERIFIED | Present; phase10/11 dependency names recorded |
| `backend/tests/adversarial/test_relationship_boundaries.py` | Adversarial unit surface | ✓ VERIFIED | 9 passed in suite |
| `backend/tests/integration/relationships/test_performance.py` | 10k / tier budgets | ✓ VERIFIED | 2 passed |
| `backend/tests/integration/relationships/test_release_gate.py` | Release authority contracts | ✓ VERIFIED | 6 passed |
| `frontend/e2e/relationships-real.spec.ts` | Real browser journeys | ✓ VERIFIED (artifact) | Present; not re-executed this session (see notes) |
| `.github/workflows/ci.yml` | CI relationship qualification | ✓ VERIFIED | Job runs release/boundaries/performance/adversarial + scope-scan artifacts |

## Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| ORM observations | `analysis_versions` / `knowledge_relation_judgments` | FK columns | ✓ WIRED | `relationship.py` FK patterns |
| `candidates.py` | accepted Phase 04 judgments | status/gate filters | ✓ WIRED | Pipeline tests; worker write path |
| `gates.py` | accepted observations | only accepted gate path | ✓ WIRED | Threshold + reason codes tested |
| `relationships.py` API | `query.py` | `require_owned_novel` | ✓ WIRED | Router mount + dependency |
| Query cutoff | `Novel.reading_progress` | `timeline_full_book` | ✓ WIRED | Phase 08 preference reuse |
| Projection | accepted PostgreSQL rows | `replay_accepted_observations` | ✓ WIRED | Projection suite passed |
| Workspace UI | `/api/relationships/{id}/graph` | `relationshipsApi` | ✓ WIRED | Contract + workspace tests |
| Analysis page | `TimelineChart` | preserved ECharts branch | ✓ WIRED | page.tsx still imports TimelineChart |
| Release CLI | independent DB + commands | `session_factory` / digests | ✓ WIRED | release_gate tests + script present |
| CI | relationship qualification | workflow step ~448+ | ✓ WIRED | junit + scope-scan artifacts |
| Phase 10/11 | query service readers only | documented methods | ✓ WIRED | scope_clean true |

## Data-Flow Trace

| Artifact | Data Variable | Source | Produces Real Data | Status |
|---|---|---|---|---|
| Worker | accepted observations | Phase 04 accepted judgments + evidence packages + gates | Yes | ✓ FLOWING |
| Query/API | nodes, edges, filters, counts, degradation | fold over accepted rows + overrides + cutoff | Yes | ✓ FLOWING |
| Projection | checksum / audit | accepted observation manifest only | Yes | ✓ FLOWING |
| Frontend graph | Cytoscape elements | server envelope nodes/edges only | Yes | ✓ FLOWING |
| Release | authority + digests | fresh session + internal command capture | Yes | ✓ FLOWING |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Full Phase 09 backend suite | `backend/.venv/Scripts/python.exe -m pytest tests/unit/relationships tests/integration/relationships tests/adversarial/test_relationship_boundaries.py -q` | **60 passed**, 0 failed, 0 skipped (~65s) | ✓ PASS |
| Frontend relationships Vitest | `npm test -- --run relationships` | **17 passed** (2 files) | ✓ PASS |
| Scope scan | `python scripts/run_relationship_qualification.py --scope-scan` | `scope_clean=true`, phase10/11 contracts present, `forbidden_hits=[]` | ✓ PASS |
| Core imports | Python import of ORM/worker/query/gates | OK; threshold 0.85; five edge types | ✓ PASS |
| Playwright real stack | `npm run test:e2e -- relationships-real.spec.ts` | **Not re-run this session** | ⚠ PARTIAL (artifact + 09-05 digest: 4 passed) |
| Full frontend unit suite / production build | `npm test -- --run` / `npm run build` | **Not re-run this session** | ⚠ PARTIAL (09-05: 85 tests + build OK; relationships subset re-verified) |
| Shell `alembic current` without test fixture env | default `.env` 5432 | **Not independently re-asserted** outside pytest PG fixtures | ⚠ PARTIAL (integration persistence suite passed against real PostgreSQL) |

Pytest emitted only known unavailable `pytest-timeout` configuration/marker warnings; no Phase 09 relationship tests failed or skipped in the independent backend suite.

## Probe Execution

No Phase 09 PLAN declares a probe script path. Step 7c is not applicable.

## Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|---|---|---|---|---|
| REQ-REL-01 | 01, 02, 05 | Evidence-gated fiction facts only | ✓ SATISFIED | Pipeline + adversarial + persistence lineage |
| REQ-REL-02 | 01, 02, 03 | Append-only temporal evolution | ✓ SATISFIED | Triggers, worker immutability, fold at narrative position |
| REQ-REL-03 | 03, 05 | Owner/version/spoiler API | ✓ SATISFIED | API + PG boundary + spoiler tests |
| REQ-REL-04 | 04, 05 | Cytoscape analysis workspace | ✓ SATISFIED | Components + Vitest; E2E artifact (prior 4 passed) |
| REQ-REL-05 | 01, 03, 05 | Protective overrides | ✓ SATISFIED | Override models/services + API tests |
| REQ-REL-06 | 02–05 | Replayable projection and release quality | ✓ SATISFIED | Projection, performance, release_gate, CLI, CI |

All REQ-REL-01..06 appear in PLAN frontmatter and 09-SPEC; none orphaned.

## Anti-Patterns Found

| File | Pattern | Severity | Impact |
|---|---|---|---|
| Repository migration state | Pre-existing Phase 07 index drift on `alembic check` (reported in 09-01 SUMMARY) | ⚠ WARNING | Outside Phase 09 ownership; Phase 09 head/tests pass under integration fixtures |
| Live Playwright / full frontend re-run | Deferred this verifier session | ℹ INFO | Does not contradict code or targeted automated re-runs; residual operational re-check only |

No Phase 09-owned implementation file was found to treat `CharacterRelation` as graph truth. No chat/clue product modules appeared in scope scan. No placeholder relationship workspace path was found under filters_required (Cytoscape intentionally not mounted).

Disconfirmation pass:

- Partial requirement among 21 must-haves: none.
- Misleading green: release negative tests assert `blocked_release` (not forged success).
- Uncovered error path: missing/tampered release evidence, over-cap filters_required, cross-owner 404, and non-accepted sources covered by executed suite.

## Human Verification Required

None blocking. Desktop/mobile Playwright exists as automated qualification (`relationships-real.spec.ts`); re-execution can be done in CI or a full-stack local pass if desired.

## Deferred Items

Phase 10 reader selection AI / multi-session conversations and Phase 11 clue tracking remain explicit non-goals (read-only contracts only). History domain support remains forbidden.

## Gaps Summary

No blocking must-have or REQ-REL gap remains. Residual notes only:

1. Playwright E2E and full frontend suite/build were not re-executed in this independent session (code present; 09-05 digests positive; relationships Vitest re-verified).
2. Pre-existing Phase 07 Alembic index drift may still appear on bare `alembic check` and is not a Phase 09 truth failure.

## Recommendation

**Proceed to Phase 10.** Phase 09 is independently verified at 21/21 must-have truths with REQ-REL-01..06 satisfied. Phase 10 must depend only on `load_filtered_relationship_graph` (and must not invent chat→relationship writes). Phase 11 may depend on `list_accepted_observation_refs` only.

---

_Verified: 2026-07-15T09:15:00Z_  
_Verifier: the agent (gsd-verifier)_
