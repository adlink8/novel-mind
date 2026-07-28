---
phase: 04-llm
plan: 04-02-candidate-packages-and-llm-judgment
subsystem: ai
tags: [knowledge-graph, llm-judgment, evidence-package, sqlalchemy, pydantic, cli]
requires:
  - phase: 04-01-knowledge-data-contracts
    provides: PostgreSQL audit contracts for runs, evidence refs, relation candidates, and judgments
provides:
  - Deterministic relation candidate drafts from text chunks with BM25/vector/adjacency/entity/time-window recall signals kept separate from graph facts
  - Bounded evidence packages with allowed evidence IDs and ontology labels for fiction/history profiles
  - Structured LLM judgment service with schema_failed, evidence_failed, and blocked paths that do not create accepted graph facts
  - CLI dry-run/write orchestration for candidate packages and judgment audit rows
affects: [04-03-evidence-gates-and-projection, 04-04-evaluation-and-domain-fixtures]
tech-stack:
  added: []
  patterns:
    - Bounded evidence package snapshots passed to LLMs instead of full novels/books
    - Recall signals stored as audit metadata, never relation confidence or accepted fact state
    - Live LLM failures persisted/reported as blocked instead of fabricated judgments
key-files:
  created:
    - backend/app/services/knowledge/__init__.py
    - backend/app/services/knowledge/candidates.py
    - backend/app/services/knowledge/evidence.py
    - backend/app/services/knowledge/llm_judge.py
    - backend/scripts/run_knowledge_graph_pipeline.py
    - backend/tests/test_knowledge_candidates.py
    - backend/tests/test_knowledge_llm_judge.py
  modified: []
key-decisions:
  - "Used text_chunk pairs as the first relation candidate evidence unit; this keeps 04-02 independent of future entity/event extraction and still avoids accepted graph facts."
  - "Kept blocked LLM outcomes as judgment audit status strings because model/schema files were outside this plan's allowed edit scope."
  - "Deduplicated persisted evidence refs per run so repeated chunks across adjacent candidates do not violate the 04-01 unique ref constraint."
patterns-established:
  - "Evidence package contract: allowed_evidence_ids are the only IDs the LLM may cite, and out-of-package refs become evidence_failed."
  - "LLM judgment contract: valid structured outputs become pending/evidence_passed or needs_human_review; no accepted status is written in 04-02."
  - "CLI contract: --dry-run performs package/judge without writes; --write creates run/candidate/evidence/judgment audit rows."
requirements-addressed: [REQ-KG-01, REQ-KG-03, REQ-KG-04]
duration: 10min
completed: 2026-07-02
---

# Phase 04 Plan 02: Candidate Packages and LLM Judgment Summary

**Deterministic evidence packages plus structured LLM relation judgments that preserve recall as signals and never write accepted graph facts.**

## Performance

- **Duration:** 10 min
- **Started:** 2026-07-02T07:13:56Z
- **Completed:** 2026-07-02T07:23:35Z
- **Tasks:** 1 implementation slice
- **Files modified:** 7

## Accomplishments

- Added knowledge service modules for deterministic chunk-pair candidate recall, bounded evidence package creation, and low-temperature LLM judgment.
- Added CLI `scripts/run_knowledge_graph_pipeline.py` with `--dry-run`, `--write`, `--limit`, and `--domain-profile fiction|history`.
- Added targeted tests proving evidence IDs are bounded, recall scores do not become confidence, fiction/history profiles diverge, invalid/out-of-package LLM outputs fail, and unavailable LLM calls become `blocked`.

## Task Commits

1. **Task 1: Candidate packages and LLM judgment** - `327e8c4` (feat)

## Files Created/Modified

- `backend/app/services/knowledge/__init__.py` - Exports knowledge package services.
- `backend/app/services/knowledge/candidates.py` - Builds deterministic relation candidate drafts from text chunks and recall signals.
- `backend/app/services/knowledge/evidence.py` - Builds bounded LLM evidence packages and ORM evidence refs.
- `backend/app/services/knowledge/llm_judge.py` - Calls `ai_service.chat()`, routes via `ai_router.route_task("extraction")`, validates schema/evidence refs, and persists judgment audits.
- `backend/scripts/run_knowledge_graph_pipeline.py` - CLI orchestration for dry-run and write modes.
- `backend/tests/test_knowledge_candidates.py` - Candidate/evidence package tests without Chroma.
- `backend/tests/test_knowledge_llm_judge.py` - LLM parser, blocked path, and persistence tests.

## Decisions Made

- Text chunks are the first package-level candidate endpoints for 04-02. They are evidence units, not accepted graph nodes; later plans can project accepted entity/event relations.
- `blocked` is written as an explicit judgment status when the LLM call is unavailable, matching the user requirement not to fake judgments. Current 04-01 Pydantic status literals do not yet include `blocked`; API exposure should reconcile that in a later plan.
- `--dry-run` is the default behavior unless `--write` is explicitly supplied, so package inspection can happen before persistence.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Deduplicated evidence refs per persisted run**
- **Found during:** Task 1 implementation.
- **Issue:** Adjacent candidate packages can reuse the same text chunk evidence; writing every package evidence row blindly would violate the unique `(run_id, ref_key)` evidence index from 04-01.
- **Fix:** Added per-run `seen_refs` tracking in the CLI write path before creating `KnowledgeEvidenceRef` rows.
- **Files modified:** `backend/scripts/run_knowledge_graph_pipeline.py`
- **Verification:** `tests/test_knowledge_models.py::test_evidence_ref_keys_are_unique_per_run` still passes, and 04-02 tests pass.
- **Committed in:** `327e8c4`

**2. [Rule 1 - Bug] Fixed order-sensitive candidate test assertion**
- **Found during:** Targeted pytest.
- **Issue:** The initial test assumed Chinese entity names returned in a specific sort order.
- **Fix:** Changed the assertion to compare sets because entity overlap order is not semantically meaningful.
- **Files modified:** `backend/tests/test_knowledge_candidates.py`
- **Verification:** `pytest tests/test_knowledge_candidates.py tests/test_knowledge_llm_judge.py -v` passed.
- **Committed in:** `327e8c4`

**Total deviations:** 2 auto-fixed (1 missing critical, 1 test bug).
**Impact on plan:** Both fixes preserve the planned scope and make write-mode persistence/test verification correct.

## Issues Encountered

- Initial verification used the wrong virtualenv relative path. Re-ran with `backend/.venv/Scripts/python.exe`.
- CLI dry-run against real data could not complete because the configured database connection was refused: `[WinError 1225] 远程计算机拒绝网络连接。`

## Verification

| Command | Result |
|---|---|
| `.\.venv\Scripts\python.exe -m py_compile app/services/knowledge/candidates.py app/services/knowledge/evidence.py app/services/knowledge/llm_judge.py scripts/run_knowledge_graph_pipeline.py tests/test_knowledge_candidates.py tests/test_knowledge_llm_judge.py` | Passed |
| `.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py tests/test_knowledge_candidates.py tests/test_knowledge_llm_judge.py -v` | Passed, 17 tests |
| `.\.venv\Scripts\python.exe -m ruff check app/services/knowledge/candidates.py app/services/knowledge/evidence.py app/services/knowledge/llm_judge.py scripts/run_knowledge_graph_pipeline.py tests/test_knowledge_candidates.py tests/test_knowledge_llm_judge.py` | Passed |
| `rg -n "…\|TODO\|FIXME\|placeholder\|coming soon\|not available" app/services/knowledge scripts/run_knowledge_graph_pipeline.py tests/test_knowledge_candidates.py tests/test_knowledge_llm_judge.py` | No matches |
| `.\.venv\Scripts\python.exe scripts/run_knowledge_graph_pipeline.py --novel-id 1 --domain-profile fiction --limit 5 --dry-run` | Failed before pipeline execution: database/network connection refused (`WinError 1225`) |

## Known Stubs

None.

## Auth Gates

None. LLM authentication/unavailability is handled as a judgment `blocked` result path and tested with a fake failing LLM call.

## User Setup Required

PostgreSQL or the configured `NOVELMIND_DATABASE_URL` target must be running and reachable before the CLI dry-run can inspect imported work. A live LLM/model configuration is also required for non-blocked judgment outputs.

## Remaining Risks

- The live CLI path was not verified end-to-end because the database service refused connections.
- `blocked` is intentionally stored as an ORM judgment status string, but 04-01 API/Pydantic literals do not yet include it. Future API/gate plans should decide whether to formalize `blocked` in public schemas.
- Vector recall is implemented defensively and degrades to non-vector signals if Chroma/Ollama is unavailable; live vector recall still needs environment verification.

## Next Phase Readiness

04-03 can consume relation candidates, package snapshots, evidence refs, and judgment rows to implement deterministic schema/evidence/threshold/conflict gates and projection. No accepted graph facts are created by 04-02.

---
*Phase: 04-llm*
*Completed: 2026-07-02*

## Self-Check: PASSED

- Found SUMMARY file.
- Found created knowledge service, CLI, and test files.
- Found implementation commit `327e8c4`.
