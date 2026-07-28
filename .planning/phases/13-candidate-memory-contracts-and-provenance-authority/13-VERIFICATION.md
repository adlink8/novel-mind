---
phase: 13-candidate-memory-contracts-and-provenance-authority
status: passed
verified_at: 2026-07-16
requirements: [V08-MEM-01, V08-MEM-02, V08-MEM-03, V08-MEM-04, V08-MEM-05]
---

# Phase 13 Verification

**status:** `passed`

## Must-Haves vs ROADMAP Success Criteria

| ID | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| V08-MEM-01 | Independent immutable `NarrativeMemoryVersion` candidate with frozen owner/novel/lineage hashes; no production promotion path | **pass** | 13-01 seven tables + append-only/seal guards; 13-02 `CandidateAuthority.create_version`; no `narrative_memory_active_pointers` |
| V08-MEM-02 | Strict typed Chapter State / Arc-Volume / Global claims; extra/summary/package-external refs fail closed | **pass** | 13-02 contracts unit matrix (16 tests); package-local validators |
| V08-MEM-03 | Claim→leaf drill-down via `Chapter.content[start:end]` re-slice; broken/broad refs fail | **pass** | `verify_source_link_closure` + Unicode seal success + stale content blocked |
| V08-MEM-04 | DAG, legal ranges, deterministic DB-row manifests | **pass** | provenance unit DAG/range suite; `compute_manifest_from_snapshot` order independence |
| V08-MEM-05 | Candidate create/validate never create/move active pointers | **pass** | fresh observer pointer snapshot equality; static capability scans |

## Plan Completion

| Plan | Status | Summary | Key commits |
| --- | --- | --- | --- |
| 13-01 | complete | Seven-table PG authority | `fb19e52`, `c11c38d`, `da2d77f`, gap-closure `b013572`, `4f386f4` |
| 13-02 | complete | Strict contracts + eligibility authority | `a452380`, `bdc515c`, `7622889` |
| 13-03 | complete | Provenance, seal, no-pointer | `59b29b8`, `11f7191`, `32338ed` |

## Commands Run and Results

```text
# 13-02 Task 3 verification
pytest tests/unit/narrative_memory/test_contracts.py \
       tests/integration/narrative_memory/test_contract_authority_pg.py -q -x
→ 20 passed

ruff check app/services/narrative_memory/contracts.py \
           app/services/narrative_memory/authority.py \
           tests/unit/narrative_memory/test_contracts.py \
           tests/integration/narrative_memory/test_contract_authority_pg.py
→ All checks passed

# 13-03 verification
pytest tests/unit/narrative_memory/test_provenance.py \
       tests/unit/narrative_memory/test_manifests.py \
       tests/integration/narrative_memory/test_provenance_pg.py \
       tests/integration/narrative_memory/test_no_pointer_side_effects.py -q
→ 19 passed

ruff check app/services/narrative_memory/provenance.py \
           app/services/narrative_memory/manifests.py \
           tests/unit/narrative_memory/test_provenance.py \
           tests/unit/narrative_memory/test_manifests.py \
           tests/integration/narrative_memory/test_provenance_pg.py \
           tests/integration/narrative_memory/test_no_pointer_side_effects.py
→ All checks passed

# Phase 13 consolidated suite (contracts/audit/authority/provenance)
pytest tests/unit/narrative_memory/ \
       tests/integration/narrative_memory/test_contract_authority_pg.py \
       tests/integration/narrative_memory/test_provenance_pg.py \
       tests/integration/narrative_memory/test_no_pointer_side_effects.py \
       tests/integration/narrative_memory/test_candidate_authority_pg.py -q
→ 60 passed, 0 skip

# Schema
python -m alembic heads
→ single head including 13memoryauth01 lineage
```

## Candidate-Only / No-Pointer Boundary

- Tables: only `narrative_memory_{versions,nodes,claims,edges,source_links,manifests,validation_reports}`
- Absent: `narrative_memory_active_pointers`, run/stage/checkpoint, promotion/rollback journals for memory
- Fresh observer: `chunk_active_pointers`, `timeline_active_pointers`, `clue_active_pointers`, `narrative_active_pointers`, `active_baselines` unchanged before/after create+seal
- Package scan (`authority.py`, `contracts.py`, `provenance.py`, `manifests.py`): no model gateway, active pointer, promotion, Chroma, Reader Chat, or current-version resolver

## Residual Risks

1. **Optional domain deep validation:** timeline/relationship/clue optional source keys are structurally required when `source_kind != hierarchy`, but full domain-table lineage revalidation is not expanded beyond Phase 07 leaf + ref presence. Acceptable for Phase 13 structural authority; Phase 14/17 may tighten.
2. **Regression suite breadth:** full Phase 07/08/09/11/12 regression commands from the plan were not re-run in this close-out wave; Phase 13 package tests + 13-01 PG suite passed. Recommend running plan `regression_commands` before Phase 14 provider work if the shared environment is dirty.
3. **Pre-existing Alembic index drift:** legacy `alembic check` may still report Phase 07/text-chunk index differences unrelated to narrative-memory tables (documented in 13-01 SUMMARY).
4. **No HTTP API in Phase 13:** persistence is service-layer only; Phase 14 owns builder entrypoints.

## Verdict

Phase 13 is **passed** for V08-MEM-01..05. Ready for Phase 14 (bottom-up candidate builder) under existing authorization, still gated by Phase 12 `provider_calls_allowed` for any provider call.

---
*Verified: 2026-07-16*
