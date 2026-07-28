---
phase: 04-llm
plan: 04-04-evaluation-and-domain-fixtures
subsystem: testing
tags: [knowledge-graph, eval, fixtures, faithfulness, cost, latency, ontology]
requires:
  - phase: 04-03-evidence-gates-and-projection
    provides: Deterministic gates, accepted judgment source-of-truth, projection boundary, and optional Neo4j no-op sync
provides:
  - Offline fiction and history fixture datasets with 20 labeled evidence-bound relation examples
  - Deterministic knowledge graph eval CLI separating recall signal quality from accepted graph fact quality
  - Cost, latency, faithfulness, schema failure, evidence gate failure, and review routing reporting
  - Architecture documentation for Phase 04 knowledge audit tables and extraction/judgment model boundary
affects: [REQ-KG-04, REQ-KG-05, REQ-KG-06, knowledge-graph-eval, architecture-docs]
tech-stack:
  added: []
  patterns:
    - Offline fixture-driven graph eval with mock/local cost and latency fields always present
    - Single evaluator pipeline for fiction/history profiles with ontology-specific relation labels
    - Optional live faithfulness check reports blocked/skipped without failing deterministic tests
key-files:
  created:
    - backend/evals/knowledge_graph_fiction_sample.json
    - backend/evals/knowledge_graph_history_sample.json
    - backend/scripts/run_knowledge_graph_eval.py
    - backend/tests/test_knowledge_eval.py
  modified:
    - docs/architecture/03-data-model.md
    - docs/architecture/08-ai-model-layer.md
key-decisions:
  - "Implemented 04-04 as an offline fixture evaluator so deterministic tests do not depend on live LLM availability."
  - "Kept recall metrics and accepted graph fact metrics separate in the report to preserve the vector/BM25/adjacency-as-signals boundary."
  - "Used the same evaluator core for fiction and history, with ontology profile and allowed relation labels supplied by each fixture."
patterns-established:
  - "Graph eval reports include recall_signal_quality, judgment_quality, accepted_graph_fact_quality, faithfulness, and cost_latency sections."
  - "Fixture judgments stand in for LLM semantic output; schema/evidence/threshold gates remain deterministic script logic."
requirements-completed: [REQ-KG-04, REQ-KG-05, REQ-KG-06]
duration: 14min
completed: 2026-07-02
---

# Phase 04 Plan 04: Evaluation and Domain Fixtures Summary

**Offline fiction/history knowledge graph fixture evaluation with evidence-bound judgments, deterministic gates, and cost/latency/faithfulness reporting.**

## Performance

- **Duration:** 14 min
- **Started:** 2026-07-02T07:55:05Z
- **Completed:** 2026-07-02T08:08:40Z
- **Tasks:** 1 implementation slice
- **Files modified:** 6

## Accomplishments

- Added 20 labeled knowledge graph examples: 10 fiction and 10 history, each with candidate recall signals, expected evidence refs, mock LLM judgment output, and expected accepted/rejected/review route.
- Added `run_knowledge_graph_eval.py` for deterministic offline evaluation of candidate coverage, schema failures, evidence gate failures, accepted precision/recall, review routing accuracy, faithfulness, cost, and latency.
- Added pytest coverage proving the same evaluator core handles fiction/history profiles while relation labels and ontology profiles differ.
- Updated architecture docs after verification passed, documenting knowledge audit tables, accepted judgment boundaries, extraction routing, fixture evals, and optional live faithfulness behavior.

## Task Commits

1. **Task 1: Evaluation and domain fixtures** - `07802fd` (feat)

## Files Created/Modified

- `backend/evals/knowledge_graph_fiction_sample.json` - Fiction fixture with 10 labeled examples across character relations, conflict, foreshadowing, sequencing, alias, and family cases.
- `backend/evals/knowledge_graph_history_sample.json` - History fixture with 10 labeled examples across person/organization, rule, succession, causality, temporal, unsupported, and source-conflict cases.
- `backend/scripts/run_knowledge_graph_eval.py` - Offline deterministic eval CLI with recall/judgment/accepted-fact/faithfulness/cost-latency report sections.
- `backend/tests/test_knowledge_eval.py` - Fixture count, ontology split, metric separation, gate failure, review routing, CLI, cost/latency, and blocked optional LLM tests.
- `docs/architecture/03-data-model.md` - Documents knowledge graph audit tables, fixture files, owner isolation, and accepted judgment source-of-truth.
- `docs/architecture/08-ai-model-layer.md` - Documents extraction task routing, LLM/script responsibility split, fixture eval reporting, and optional live faithfulness behavior.

## Decisions Made

- Deterministic fixture tests remain fully offline. Live LLM faithfulness is represented as an optional mode that reports `blocked` when no live model is configured.
- Accepted precision is computed only after deterministic gates pass; recall signal quality is reported separately and never treated as graph truth.
- The CLI accepts fixture paths from either repo root or `backend` working directory so the plan's PowerShell verification command works unchanged.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed direct CLI import path**
- **Found during:** Task 1 pytest CLI subprocess coverage.
- **Issue:** `python scripts/run_knowledge_graph_eval.py ...` from `backend` failed with `ModuleNotFoundError: No module named 'app'` because Python placed `backend/scripts` on `sys.path`.
- **Fix:** Added the backend root to `sys.path` at script startup and marked the following app imports with targeted `# noqa: E402`.
- **Files modified:** `backend/scripts/run_knowledge_graph_eval.py`
- **Verification:** `pytest tests/test_knowledge_eval.py -v`, both fixture dry-runs, and `ruff check` passed.
- **Committed in:** `07802fd`

**Total deviations:** 1 auto-fixed blocking issue.
**Impact on plan:** Required for the plan's documented CLI verification command; no scope expansion.

## Issues Encountered

- `docs/architecture/03-data-model.md` already contained uncommitted RAG eval documentation changes before 04-04 edits. The file was preserved and Phase 04 content was appended against the current state; no rollback was attempted.
- The worktree still contains many unrelated pre-existing modified/untracked files from other phases. They were left untouched.
- `gsd-sdk query state.update-progress`, `state.record-metric`, `state.add-decision`, `state.record-session`, and `requirements.mark-complete` did not recognize this project's nested STATE/REQUIREMENTS markdown structure. `state.advance-plan` and `roadmap.update-plan-progress` succeeded; remaining metadata was updated manually in the existing file format.

## Verification

| Command | Result |
|---|---|
| `.\.venv\Scripts\python.exe -m py_compile scripts\run_knowledge_graph_eval.py tests\test_knowledge_eval.py` | Passed |
| `.\.venv\Scripts\python.exe -m pytest tests\test_knowledge_eval.py -v` | Passed, 5 tests |
| `.\.venv\Scripts\python.exe scripts\run_knowledge_graph_eval.py --fixture backend/evals/knowledge_graph_fiction_sample.json --dry-run` | Passed, success true, 10 examples |
| `.\.venv\Scripts\python.exe scripts\run_knowledge_graph_eval.py --fixture backend/evals/knowledge_graph_history_sample.json --dry-run` | Passed, success true, 10 examples |
| `.\.venv\Scripts\python.exe -m ruff check scripts\run_knowledge_graph_eval.py tests\test_knowledge_eval.py` | Passed |
| `.\.venv\Scripts\python.exe -m json.tool evals\knowledge_graph_fiction_sample.json` | Passed |
| `.\.venv\Scripts\python.exe -m json.tool evals\knowledge_graph_history_sample.json` | Passed |
| `rg -n 'TODO\|FIXME\|placeholder\|coming soon\|not available\|=\[\]\|=\{\}\|=null\|=""' ...` | No matches |

## Known Stubs

None.

## Auth Gates

None. Live LLM faithfulness is optional and reports blocked/skipped when unavailable.

## User Setup Required

None for deterministic fixture evaluation. Optional live faithfulness checking requires a configured live model and `NOVELMIND_ENABLE_LIVE_KG_FAITHFULNESS=1`; it is not required for tests.

## Remaining Risks

- Fixture metrics are regression coverage, not a replacement for human-labeled production corpora.
- Cost and latency are mock/local estimates in these fixtures (`0` values by design); live runs should populate real usage once model configuration is available.
- Existing unrelated worktree changes remain outside this plan.

## Next Phase Readiness

Phase 04 now has fixture/eval coverage for fiction/history domain portability, evidence-bound judgments, deterministic gates, optional LLM-unavailable behavior, and cost/latency reporting. A later phase can add production-scale graph eval datasets or a live faithfulness judge without changing the deterministic fixture contract.

---
*Phase: 04-llm*
*Completed: 2026-07-02*

## Self-Check: PASSED

- Found SUMMARY file.
- Found fiction and history fixture files.
- Found eval CLI file.
- Found implementation commit `07802fd`.
