# 06-01 Summary — Test Taxonomy & Deterministic Quality Foundation

**Status:** COMPLETE  
**Date:** 2026-07-12  
**Plan:** `.planning/phases/06-automated-quality-ci/06-01-PLAN.md`  
**Decisions:** D-04, D-09, D-10, D-16

## What Was Done

### Slice 1 — Backend classification and semantic fail-closed

- Registered primary markers `unit | integration | contract | live` plus scope combinator `e2e` in `backend/pytest.ini` with `--strict-markers` / `--strict-config`.
- Removed default `addopts = -m "not e2e"` so e2e is never implicitly excluded.
- Collection gate in `backend/tests/conftest.py` fails closed when a test lacks a primary marker.
- Applied per-marker timeouts (D-16): unit 5s, contract 15s, integration 30s, live 180s.
- Marked all existing backend modules with a primary marker (mostly `unit`).
- `test_rag_e2e.py` → `live + e2e`; deleted random embedding fallback on semantic/live paths; Ollama unavailable → `blocked_dependency` skip (`metrics=null`, `quality_comparable=false`).
- Random/fixed vectors remain only in mock vector-store unit contract shape tests (`test_vector_store.py`).
- Subprocess CLI smoke reclassified as `integration` so unit 5s budget is not violated.

### Slice 2 — Coverage, timeout, JUnit, flake policy

- Locked tools: `pytest-cov==7.1.0`, `pytest-timeout==2.4.0`, `vitest==4.1.10`, `@vitest/coverage-v8==4.1.10`.
- Encoded fail-closed policy in `.quality/coverage-policy.yml` + JSON Schema.
- Contract tests: `backend/tests/test_test_policy.py` (schema, thresholds, globs, flake, timeouts, negative cases).
- Frontend contract: `frontend/src/__tests__/coverage-policy.test.ts` + `quality-thresholds.ts`.
- Vitest `test:coverage` script emits LCOV/JSON under `frontend/coverage/`.
- Backend JUnit + Cobertura-style coverage XML under `backend/artifacts/`.

## Files Changed

| Path | Role |
|------|------|
| `backend/pytest.ini` | Markers, strict config, timeout defaults |
| `backend/requirements-dev.txt` | Locked pytest-cov / pytest-timeout |
| `backend/tests/conftest.py` | Classification gate + marker timeouts |
| `backend/tests/test_*.py` | Module-level primary markers |
| `backend/tests/test_rag_e2e.py` | live+e2e, no random semantic fallback |
| `backend/tests/test_test_policy.py` | Policy contract tests |
| `backend/tests/fixtures/coverage-policy-*.yml` | Negative policy fixtures |
| `backend/artifacts/.gitkeep` | Artifact directory |
| `.quality/coverage-policy.yml` | Locked D-09/D-10/D-16 policy |
| `.quality/coverage-policy.schema.json` | Fail-closed schema |
| `frontend/package.json` / `package-lock.json` | vitest 4.1.10 + coverage-v8 |
| `frontend/vitest.config.ts` | Coverage reporters + testTimeout |
| `frontend/src/__tests__/coverage-policy.test.ts` | Frontend policy locks |
| `frontend/src/__tests__/quality-thresholds.ts` | Shared threshold constants |

## Verification

```text
# Collection (strict markers + classification gate)
cd backend
pytest --collect-only -q --strict-markers
# → 394 tests collected

# Uncategorized probe
# → UsageError: Test classification gate failed (D-04)

# Classification filter
pytest -m "unit or contract" tests/test_vector_store.py tests/test_rag_e2e.py --junitxml=artifacts/classification.xml
# → 20 passed, 12 deselected (live e2e not selected)

# Policy contract
pytest tests/test_test_policy.py -m contract --junitxml=artifacts/policy.xml
# → 16 passed

# Unit/contract + coverage + JUnit
pytest -m "unit or contract" --cov=app --cov-branch --cov-report=xml:artifacts/backend-coverage.xml --junitxml=artifacts/backend-junit.xml
# → 369 passed, 25 deselected; artifacts written

# Frontend
cd frontend && npm run test:coverage
# → 29 passed (3 files); vitest 4.1.10; coverage report + lcov
```

## Deviations

1. **CLI subprocess tests → `integration`:** `test_knowledge_unit_cli.py` (and reconcile subprocess case) exceed the unit 5s budget; reclassified to integration (30s) rather than relaxing D-16 unit timeout.
2. **Vitest full-tree thresholds not auto-failing:** Policy locks overall frontend line>=75 / branch>=65, but current suite coverage is ~3% of the tree. Local `test:coverage` generates reports without failing the incomplete suite; fail-closed enforcement is via schema/policy contract tests and later CI gate. Threshold numbers are asserted in `coverage-policy.test.ts`.
3. **Coverage artifacts not committed:** Generated JUnit/XML/LCOV stay local under `backend/artifacts/` and `frontend/coverage/`; only `.gitkeep` is tracked.

## Commit Hashes

(filled after commit)

## Next

- Do **not** start 06-02 from this plan execution.
- 06-02: PostgreSQL/Chroma real service integration (depends on 06-01).
