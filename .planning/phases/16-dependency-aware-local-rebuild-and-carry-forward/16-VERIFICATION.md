---
phase: 16-dependency-aware-local-rebuild-and-carry-forward
status: passed
verified_at: 2026-07-16
requirements: [V08-REUSE-01, V08-REUSE-02, V08-REUSE-03, V08-REUSE-04]
---

# Phase 16 Verification

**status:** `passed`

## Must-Haves vs Requirements

| ID | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| V08-REUSE-01 | Dependency-aware local dirty closure from explicit parent/target authority | **pass** | `dependency_graph.py` + `change_oracle.py` + unit/PG |
| V08-REUSE-02 | Checksum carry-forward; dirty-only Phase 14 stages; no stage for `carried` | **pass** | `carry_forward.py` + `rebuild_executor.py` + carry/local PG |
| V08-REUSE-03 | Conservative expansion; candidate-only; no pointer/promotion | **pass** | adversarial + CI static scans; PG observers |
| V08-REUSE-04 | Auditable reuse economics (observed vs upper bound vs carry vs cache) | **pass** | `reuse_report.py` + unit formulas + PG recompute + CLI report |

## Plan Completion

| Plan | Status | Summary |
| --- | --- | --- |
| 16-01 | complete | Authority tables, graph, oracle |
| 16-02 | complete | Carry-forward + dirty-only executor |
| 16-03 | complete | Report, CLI, adversarial, CI |

## Commands and Results

```text
cd backend
.\.venv\Scripts\python.exe -m pytest \
  tests/unit/narrative_memory/test_dependency_graph.py \
  tests/unit/narrative_memory/test_change_oracle.py \
  tests/unit/narrative_memory/test_carry_forward.py \
  tests/unit/narrative_memory/test_reuse_report.py \
  tests/integration/narrative_memory/test_rebuild_authority_pg.py \
  tests/integration/narrative_memory/test_change_oracle_pg.py \
  tests/integration/narrative_memory/test_carry_forward_pg.py \
  tests/integration/narrative_memory/test_local_rebuild_pg.py \
  tests/integration/narrative_memory/test_reuse_report_pg.py \
  tests/integration/narrative_memory/test_rebuild_cli_pg.py \
  tests/adversarial/test_narrative_memory_rebuild_safety.py \
  tests/ci/test_narrative_memory_rebuild_contract.py -q

# 62 passed

.\.venv\Scripts\python.exe -m alembic heads
# 16memrebuild01 (head)

.\.venv\Scripts\ruff.exe check <Phase 16 production + test paths>
# All checks passed
```

## Safety Properties Confirmed

1. Oracle / graph / carry / executor / report are provider-free (capability flags + AST).
2. Only Phase 14 dirty stage keys may receive stages; carried items create none.
3. CLI requires explicit parent+target versions; rejects promote/current/embedding/chat options.
4. Reuse report recomputes from durable rows; carry count never inferred from stages.
5. Parent candidate rows remain immutable across plan/carry/materialize in PG fixtures.
6. No FastAPI product route; no production pointer mutation in Phase 16 modules.

## Residual Risks / Gaps

- **Conservative edit expansion:** force-full hierarchy rebuild can mark unedited chapters evidence-remap / MAPPING_UNPROVEN and expand the dirty suffix, so partial carry of unaffected chapters is not always observed under current fixtures (safety-preserving, may over-rebuild).
- **Claim composite checksum:** `claim_checksum` includes package-local `source_keys` which are not persisted on link rows; carry preserves typed payload fields and node content checksums; full claim hash may differ after rebind.
- **Dirty execution depth:** tests materialize dirty stages but do not run a full controlled Phase 14 provider worker end-to-end for every edit fixture (worker remains Phase 14-owned).
- Host `alembic check` against non-test DBs may report not-up-to-date until operators upgrade those environments.

## Verdict

Phase 16 is **passed** for candidate-only dependency-aware local rebuild, carry-forward, reuse reporting, and fixed explicit-version CLI with 62 targeted tests green.
