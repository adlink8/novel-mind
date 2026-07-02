---
status: passed
phase: 04-llm
created: 2026-07-02
verified_by: inline-gsd-verifier
source:
  - .planning/phases/04-llm/04-01-knowledge-data-contracts-SUMMARY.md
  - .planning/phases/04-llm/04-02-candidate-packages-and-llm-judgment-SUMMARY.md
  - .planning/phases/04-llm/04-03-evidence-gates-and-projection-SUMMARY.md
  - .planning/phases/04-llm/04-04-evaluation-and-domain-fixtures-SUMMARY.md
---

# Phase 04 Verification: LLM 语义判定与证据门控知识图谱链路

## Verdict

PASSED.

Phase 04 achieved its goal: NovelMind now has an auditable knowledge graph construction chain where scripts own recall, evidence packaging, schema/evidence/threshold/conflict gates, persistence, and projection; LLM participation is limited to semantic proposal/judgment; fiction and history share the same pipeline through domain and ontology profiles.

## Requirement Verification

| Requirement | Status | Evidence |
|---|---|---|
| REQ-KG-01 | VERIFIED | `backend/app/services/knowledge/candidates.py`, `evidence.py`, `llm_judge.py`, and `gates.py` separate deterministic recall/package/gate/write logic from `ai_service.chat()` semantic judgment. |
| REQ-KG-02 | VERIFIED | `KnowledgeEvidenceRef`, `KnowledgeRelationCandidate.evidence_refs`, `KnowledgeRelationJudgment.evidence_refs`, evidence package `allowed_evidence_ids`, and gate tests enforce evidence-bound candidates and judgments. |
| REQ-KG-03 | VERIFIED | Candidate and eval code store `recall_signals`; tests and eval report separate `recall_signal_quality` from `accepted_graph_fact_quality`; projection reads only accepted judgments. |
| REQ-KG-04 | VERIFIED | `domain_profile` / `ontology_profile` support `fiction` and `history`; fixture tests verify same core pipeline with different ontology labels. |
| REQ-KG-05 | VERIFIED | PostgreSQL audit tables and accepted `KnowledgeRelationJudgment` rows are source of truth; `graph_sync.py` Neo4j path is disabled by default and no-op when disabled. |
| REQ-KG-06 | VERIFIED | 20 labeled examples, offline eval CLI, cost/latency fields, faithfulness section, schema/evidence/review routing metrics, and deterministic tests are present. |

## Decision Coverage

| Decision | Status | Evidence |
|---|---|---|
| D-01 | VERIFIED | LLM calls are isolated in `llm_judge.py`; gates, projection, CLI, and eval are script-owned. |
| D-02 | VERIFIED | Out-of-package evidence becomes `evidence_failed`; accepted gates require same-owner, same-run evidence refs. |
| D-03 | VERIFIED | Recall signals are package metadata and eval metrics only; accepted projection requires `status='accepted'` and `gate_status='accepted'`. |
| D-04 | VERIFIED | Fiction/history fixtures and ontology profiles use the same evaluator and pipeline steps. |
| D-05 | VERIFIED | Neo4j sync reads accepted PostgreSQL rows and is disabled by default; failed/skipped sync does not mutate PostgreSQL accepted state. |
| D-06 | VERIFIED | CLI/run status paths exist for pipeline and eval; LLM unavailable path records `blocked` instead of fake judgments. |
| D-07 | VERIFIED | Implementation names differ from planning placeholders where useful, but evidence, owner isolation, reviewability, and deterministic gates are preserved. |

## Automated Checks

| Command | Result |
|---|---|
| `pytest tests/test_knowledge_models.py tests/test_knowledge_candidates.py tests/test_knowledge_llm_judge.py tests/test_knowledge_gates.py tests/test_knowledge_projection.py tests/test_knowledge_api.py tests/test_knowledge_eval.py -v` | PASSED: 34 tests |
| `python scripts/run_knowledge_graph_eval.py --fixture backend/evals/knowledge_graph_fiction_sample.json --dry-run` | PASSED: `success=true`, 10 examples |
| `python scripts/run_knowledge_graph_eval.py --fixture backend/evals/knowledge_graph_history_sample.json --dry-run` | PASSED: `success=true`, 10 examples |
| `gsd-sdk query verify.schema-drift 04` | PASSED: `drift_detected=false` |
| `gsd-sdk query verify.codebase-drift 04` | WARNING only: structural mapping drift detected outside this phase |
| `pytest tests -m "not e2e" -q` | ENVIRONMENT WARNING: reached 272 passed, then Windows `WinError 10055`; failing test passed on isolated rerun |
| `pytest tests/test_novels.py::test_chapters_list_not_found -q` | PASSED |
| `pytest tests/test_novels.py -q` | PASSED: 19 tests |

## Residual Risks

- Online PostgreSQL migration execution was not re-run because the configured PostgreSQL service refused connections during plan execution; offline Alembic SQL/head checks passed in 04-01.
- Live LLM and live vector/Neo4j end-to-end paths remain environment-dependent. Deterministic tests cover unavailable-model behavior and Neo4j disabled behavior.
- Codebase drift gate recommends refreshing mapping for structural docs and repository files. This is a planning-context warning, not a Phase 04 functional blocker.
- v0.3 RAG eval quality gaps remain open and are explicitly outside Phase 04 completion.

## Status

Phase 04 is verified as passed. No gap-closure plan is required for Phase 04.
