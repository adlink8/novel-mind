# 06-02 Summary — PostgreSQL 16 & Chroma CI Service Locks

**Status:** COMPLETE  
**Date:** 2026-07-12  
**Plan:** `.planning/phases/06-automated-quality-ci/06-02-PLAN.md`  
**Decisions:** D-05, D-07 (blocked_dependency / no quality scores on outage)

## What Was Done

### Slice 1 — PostgreSQL 16 migration & semantics

- Resolved registry digest for `postgres:16.10`:
  - `sha256:21f6013073bc6b92830a2129570e2f5ec42a6c734b5a985a41e83aa58f54c3c1`
- Created isolated CI compose topology (`docker-compose.ci.yml`, project `novelmind-ci`):
  - Postgres host port **5433**, DB `novelmind_ci`, volume `ci_pgdata`
  - Healthcheck: `pg_isready`
- Wrote fail-closed lock manifest `.github/ci/service-lock.json` (tag + digest + URLs + health).
- Integration fixtures: `backend/tests/integration/conftest.py`
  - Digest present + compose drift checks fail closed
  - Schema reset via `DROP SCHEMA public CASCADE` on dedicated CI DB only
  - Alembic subprocess runner with CI `NOVELMIND_DATABASE_URL`
  - Prefer `127.0.0.1` over `localhost` (Windows IPv6 `::1` connect hang with Docker IPv4 publish)
- Tests:
  - `test_postgres_migrations.py` — heads, empty upgrade/current/check/history, historical `c2860beb647d` → heads, PG16 guard (not SQLite)
  - `test_postgres_semantics.py` — tsvector/GIN, uniqueness/FK, owner isolation, import lease exclusivity, promotion journal unique key, concurrent username race

### Slice 2 — Chroma contract, outage, recovery

- Locked image exactly as RESEARCH:
  - `chromadb/chroma:1.5.9@sha256:abcce7c335e2dab9f11ef629296f7309b09cb19ae4b34da32ac7e34ff5773140`
- Health: `/api/v2/heartbeat` (compose healthcheck via bash `/dev/tcp` HTTP probe; image has no curl)
- Host port **8002**, volume `ci_chromadata`
- Pinned Python client `chromadb==1.5.9` in `backend/requirements.txt` (already installed in venv)
- Tests:
  - `test_chroma_contract.py` — digest/client pin, heartbeat, fixed-vector CRUD, named-collection reconcile
  - `test_chroma_recovery.py` — timeout/bad response/docker stop; first failure evidence under `backend/artifacts/`; max retry 1; `metrics=null` / `blocked_dependency` / `quality_comparable=false`; recovery idempotent (no duplicate count)

## Files Changed

| Path | Role |
|------|------|
| `docker-compose.ci.yml` | CI-locked Postgres 16.10 + Chroma 1.5.9 digests, isolated ports/volumes |
| `.github/ci/service-lock.json` | Fail-closed service lock manifest |
| `backend/requirements.txt` | `chromadb==1.5.9` |
| `backend/tests/integration/__init__.py` | Package marker |
| `backend/tests/integration/conftest.py` | Real-service fixtures + lock validation |
| `backend/tests/integration/test_postgres_migrations.py` | Migration matrix on real PG16 |
| `backend/tests/integration/test_postgres_semantics.py` | tsvector/constraints/concurrency |
| `backend/tests/integration/test_chroma_contract.py` | Fixed-vector store contract |
| `backend/tests/integration/test_chroma_recovery.py` | Outage evidence + recovery |

## Verification

```text
# Services
docker compose -f docker-compose.ci.yml up -d --wait db chroma
# → db Healthy, chroma Healthy

# Alembic against CI Postgres (NOVELMIND_DATABASE_URL → :5433/novelmind_ci)
cd backend
alembic heads          # → e5b8c20d4a73 (head)
alembic upgrade heads  # full chain from empty
alembic current        # → e5b8c20d4a73 (head)
alembic check          # → No new upgrade operations detected
alembic history        # base → head present

# PostgreSQL integration
pytest -m integration tests/integration/test_postgres_migrations.py \
  tests/integration/test_postgres_semantics.py \
  --junitxml=artifacts/postgres.xml --timeout=30
# → 12 passed

# Chroma integration
pytest -m integration tests/integration/test_chroma_contract.py \
  tests/integration/test_chroma_recovery.py \
  --junitxml=artifacts/chroma.xml --timeout=30
# → 8 passed
```

## Deviations

1. **Host ports 5433/8002** instead of 5432/8001 so developer `docker-compose.yml` can remain up without clash.
2. **`127.0.0.1` in lock URLs** — Windows + Docker Desktop can hang on `localhost` → `::1` when only IPv4 is published.
3. **Chroma healthcheck** uses bash `/dev/tcp` HTTP GET because the image has no curl/wget/python; tests still assert `/api/v2/heartbeat` via httpx.
4. **Developer compose still uses `pgvector/pgvector:pg16` and `chromadb/chroma:latest`** — intentionally not changed; CI topology is separate. Pinning dev compose is out of scope for 06-02.
5. **Generated failure evidence JSON and JUnit XML** stay local under `backend/artifacts/` (not committed).

## Commit Hashes

- `4954a26` — `feat(06-02): pin Postgres 16/Chroma digests and real-service integration tests`
- docs commit: `docs(06-02): add plan summary and update execution state` (this file)

## Next

- Do **not** start 06-03 from this plan execution.
- 06-03: frozen fixtures, adversarial contracts, Generator/Judge isolation & calibration.
