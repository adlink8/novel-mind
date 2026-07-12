---
phase: 05-narrative-knowledge-unit-layer
verified: 2026-07-12T03:39:31Z
status: passed
score: 8/8 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 7/8
  gaps_closed:
    - "REQ-NU-06 / D-07: production API and frozen candidate evaluation now invoke the same NarrativeRetrievalStrategy; only the build selector differs"
  gaps_remaining: []
  regressions: []
---

# Phase 05: Narrative Knowledge Unit Layer — Fifth Final Independent Verification

**Phase Goal:** 将 Phase 04 的 accepted judgments 蒸馏为可追溯、可版本化、可评测和可回滚的叙事知识单元检索层，同时保留原始 chunk 混合召回。
**Verified:** 2026-07-12T03:39:31Z
**Status:** `passed`
**Re-verification:** Yes — full 8/8 regression after gap closure

## Goal Achievement

### Observable Truths / REQ-NU-01..08

| # | Truth | Status | Code and runtime evidence |
|---|---|---|---|
| 1 | REQ-NU-01: accepted judgments enter a versioned draft/canonical pipeline; gates never publish | ✓ VERIFIED | `source_snapshot.py` selects accepted + gate-accepted rows; materialization is script-owned; no publication call was found in Phase 04 `gates.py`; source/materialize tests pass. |
| 2 | REQ-NU-02: every unit retains owner/work/domain/judgment/evidence lineage | ✓ VERIFIED | Composite scoped contracts and evidence links exist in models/migration and are enforced by snapshot/materialization/index tests, including wrong-owner rejection. |
| 3 | REQ-NU-03: conservative canonicalization handles conflicts/time/lifecycle with zero hard-negative false merges | ✓ VERIFIED | Deterministic canonical keys and conflict, direction, identity, temporal and lifecycle gates are substantive; canonicalization fixtures pass. |
| 4 | REQ-NU-04: PostgreSQL owns truth; Chroma candidate collections are immutable/replayable | ✓ VERIFIED | Builds, pointers, journals and watermarks are PostgreSQL models; checksum-named collections and actual-ID/metadata reconcile pass; live Chroma metadata round-trip passed. |
| 5 | REQ-NU-05: chunks/units/hybrid exist with raw fallback and citations | ✓ VERIFIED | All three modes run through `NarrativeRetrievalStrategy`; hybrid fusion preserves source identity/evidence refs and returns chunk fallback when units are empty; default remains chunks. |
| 6 | REQ-NU-06: candidate-bound fiction/history eval, faithfulness/latency/canary/reconcile gates, and production parity | ✓ VERIFIED | API global/novel endpoints inject `production_retrieval_strategy`; evaluator calls the same strategy with `select_candidate_build`, while API defaults to `select_active_build`. Parity tests cover ranking, lifecycle, citations, fallback, global/novel, owner scope and 401/403. |
| 7 | REQ-NU-07: signed prepare/commit promotion and joint rollback/restore | ✓ VERIFIED | `promotion-evidence.v2` canonical HMAC envelope is reverified and tamper tests reject substitutions; production CLIs reject `--evidence-secret` and require env secret; direct Chroma checkpoint probe is mandatory before rollback/restore. |
| 8 | REQ-NU-08: hash-scoped incremental refresh, zero-write no-change, durable resume and final-only publication | ✓ VERIFIED | Hash deltas select affected subjects; no-change counters remain zero; indexed/promoted/post-reconciled interruptions commit durable checkpoints and resume in a new session without duplicate build/journal/index work; pointer/watermark commit last. |

**Score:** 8/8 truths verified.

### D-01..D-10

| Decision | Status | Evidence |
|---|---|---|
| D-01 | VERIFIED | Only accepted and gate-accepted Phase 04 judgments seed snapshots. |
| D-02 | VERIFIED | Acceptance and retrieval publication remain separate transactions/runs. |
| D-03 | VERIFIED | PostgreSQL truth and immutable, replayable Chroma projection verified. |
| D-04 | VERIFIED | Owner/work/domain/judgment/evidence/build lineage enforced. |
| D-05 | VERIFIED | Deterministic-first conservative merge and hard-negative gates pass. |
| D-06 | VERIFIED | Explicit chunks/units/hybrid, citations and raw fallback pass. |
| D-07 | VERIFIED | Frozen candidate evaluation and production API share one strategy boundary; candidate selector is the sole policy difference. |
| D-08 | VERIFIED | Exact signed evidence plus pointer/manifest/watermark rollback and restore pass. |
| D-09 | VERIFIED | Hash/lifecycle deltas, zero-write path and three fresh-session resume points pass. |
| D-10 | VERIFIED | Scripts own construction, validation, evaluation gates, publication and rollback; no new uncontrolled LLM path found. |

## Five PLAN Must-Haves

| Plan | Status | Assessment |
|---|---|---|
| 05-01 | VERIFIED | Acceptance/publication separation, immutable snapshots, mandatory lineage and lifecycle contracts. |
| 05-02 | VERIFIED | Script-owned deterministic construction, conservative merge, disputed visibility and zero hard-negative merges. |
| 05-03 | VERIFIED | Immutable candidate indexing does not move pointer; existing chunks remain; modes/citations/fallback/outage handling pass. |
| 05-04 | VERIFIED | Frozen fixtures, real candidate-bound strategy, signed evidence, canary/reconcile/human-approval gates and recoverable journal pass. |
| 05-05 | VERIFIED | Hash scope, stale exclusion, durable final-only publication, scoped reconcile and mandatory rollback/restore drill pass. |

## Critical Counterproof Review

### Shared retrieval boundary and API parity

- `backend/app/api/search.py` contains no `fuse_results`, `hybrid_search_service`, `narrative_search_service`, or mode-routing branch beyond the unauthenticated units/hybrid 401 guard.
- Global and novel endpoints call `NarrativeRetrievalStrategy.search_global/search_novel` through FastAPI dependency injection.
- `candidate_retriever` invokes that same strategy and supplies `select_candidate_build(build)`; normal production calls use `select_active_build`.
- `test_api_and_candidate_eval_share_strategy_boundary_and_parity`, fallback, global owner scope, unauthenticated 401, and cross-owner 403 tests passed.

### Previously passed critical items

- Real candidate-bound eval: candidate identity/checksum/collection and owner/novel/lifecycle metadata are bound to the selected immutable build.
- Promotion evidence: `promotion-evidence.v2` tamper rejection and environment-only signing secret behavior pass.
- Chroma recovery: rollback and restore require direct collection ID/metadata probes; missing or mismatched checkpoints fail before state mutation.
- Durable refresh: fresh-session resume after `indexed`, `promoted`, and `post_reconciled` produces no duplicate artifacts.
- Scoped reconcile: pointer/owner/novel/domain filters must resolve exactly one active pointer; ambiguous scope fails closed.
- Exact PLAN CLI: all seven entrypoints expose working parsers; exact 05-02 command executes against a temporary frozen snapshot; documented index and rollback dry-runs pass.

## Required Artifacts and Key Links

| Artifact / link | Exists & substantive | Wiring/data flow | Status |
|---|---|---|---|
| Models/migration → snapshot → materialization | Yes | Accepted PostgreSQL rows become immutable evidence-bound units | VERIFIED |
| Canonicalization/lifecycle → candidate index | Yes | Gated canonical rows feed immutable manifests; stale lifecycle excluded | VERIFIED |
| API → `NarrativeRetrievalStrategy` | Yes | Global and novel API calls use shared strategy | VERIFIED |
| Eval → same strategy + candidate selector | Yes | Candidate build selected without active pointer mutation | VERIFIED |
| Signed eval/canary/reconcile → promotion journal | Yes | Complete v2 envelope reverified at prepare/replay/commit | VERIFIED |
| Refresh checkpoints → reconcile → pointer/watermark | Yes | Durable staged commits; pointer/watermark are final | VERIFIED |
| Rollback/restore → Chroma checkpoint | Yes | Direct collection and manifest/build metadata required | VERIFIED |

## Data-Flow Trace (Level 4)

| Artifact | Data source | Produces real data | Status |
|---|---|---|---|
| Search API | Active PostgreSQL pointer + chunk DB/Chroma + active unit Chroma collection | Yes; strategy output serialized by API | FLOWING |
| Frozen eval | Explicit candidate build + frozen fixture queries | Yes; candidate selector feeds the same retrieval policy | FLOWING |
| Promotion | Signed domain reports + reconcile + canary + approval | Yes; exact evidence creates journal then pointer update | FLOWING |
| Incremental refresh | Committed watermark versus accepted judgment/evidence hashes | Yes; affected subjects rebuild into fresh candidate | FLOWING |

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Phase 05 + hybrid | Explicit 12-file pytest suite | 103 passed in 19.69s | PASS |
| Backend regression | `.venv\\Scripts\\python.exe -m pytest tests -m "not e2e" -q` | 366 passed, 12 deselected | PASS |
| Exact CLI contracts | `pytest tests/test_knowledge_unit_cli.py -q` | 13 passed, including real subprocess frozen-snapshot 05-02 | PASS |
| Ruff | `python -m ruff check app scripts tests` | All checks passed | PASS |
| Compile | `python -m compileall -q app scripts tests` | exit 0 | PASS |
| Alembic | heads; offline SQL; online upgrade/current/check | one head `e5b8c20d4a73`; current; no new operations | PASS |
| Live Chroma | heartbeat + temporary collection add/get/delete | owner/novel/build/manifest/lifecycle metadata round-trip passed; collection deleted | PASS |
| Exact sample-ID commands | Five PLAN commands | index/rollback pass; missing snapshot/active pointer commands fail closed because operator DB lacks sample IDs | PASS / ENV DATA |

## Probe Execution

No phase-declared or conventional `probe-*.sh` exists. Required runtime probes were executed through the test suite and live Chroma smoke; none was substituted with SUMMARY narration.

## Requirements Coverage

| Requirement range | Source plans | Status | Evidence |
|---|---|---|---|
| REQ-NU-01..04 | 05-01, 05-02, 05-03, 05-05 | SATISFIED | Snapshot, lineage, canonicalization, immutable projection and PostgreSQL truth checks above. |
| REQ-NU-05..07 | 05-03, 05-04, 05-05 | SATISFIED | Shared retrieval, candidate evaluation, signed promotion and recovery checks above. |
| REQ-NU-08 | 05-05 | SATISFIED | Hash-scoped refresh, zero-write, durable resume and lifecycle residue checks above. |

All Phase 05 plan IDs are covered; no orphan Phase 05 requirement was found. REQUIREMENTS.md status labels remain planning metadata and were not treated as evidence.

## Commit and Worktree Boundary

| Commit | Check | Assessment |
|---|---|---|
| `0c1391b` | `git show --check 0c1391b` | Clean. Focused runtime/test change; closes the shared-strategy blocker and preserves 401/403. |
| `2132880` | `git show --check 2132880` | Clean. Documentation/state only; not used as implementation evidence. |

The worktree contained many pre-existing modified/untracked files before verification. This verifier changed only this report and the temporary live Chroma collection, which was deleted. No commit was created.

## Anti-Patterns Found

| Scope | Pattern | Severity | Impact |
|---|---|---|---|
| Phase 05 runtime files | Unreferenced `TBD` / `FIXME` / `XXX` | None | Debt-marker gate clear. |
| Search API | Duplicated chunks/units/hybrid fusion | None | Routing is centralized in the shared strategy. |
| Production secrets | CLI/default signing secret | None | Override forbidden; write paths require environment secret. |

## Human Verification Required

None for phase status. The AI-SPEC requires operator approval before the first real production active-pointer cutover; that is an operational release gate already enforced by promotion evidence, not an unresolved implementation uncertainty.

## Gaps Summary

No reproducible blocker remains. The fourth-round REQ-NU-06/D-07 wiring gap is closed by `0c1391b`, and all previously passing requirements regressed cleanly under independent tests and live PostgreSQL/Chroma checks. Phase goal is achieved.

---

_Verified: 2026-07-12T03:39:31Z_
_Verifier: Codex — fifth final independent goal-backward verification_
