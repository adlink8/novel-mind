---
last_mapped_commit: b679b49
---

# Testing Patterns

**Analysis Date:** 2026-08-07

## Test Framework

### Backend

**Runner:**
- pytest 8.3+ with `pytest-asyncio`, configured by `backend/pytest.ini`.
- `pytest-cov==7.1.0` and `pytest-timeout==2.4.0` are locked in `backend/requirements-dev.txt` and `.quality/coverage-policy.yml`.
- Assertions use native Python `assert`; exception checks use `pytest.raises`.

**Run commands:**

```bash
cd backend
pytest                                      # all collected backend tests
pytest -m "unit or contract" --cov=app --cov-branch
pytest -m integration                       # service-backed integration scope
pytest -m live                              # external dependency scope
pytest tests/test_test_policy.py -m contract -q
```

`backend/pytest.ini` enables strict markers/config, a 30-second default timeout, and does not implicitly exclude e2e/live tests. Always select expensive categories explicitly in local focused runs.

### Frontend unit/component

**Runner:**
- Vitest 4.1.10 with jsdom and React Testing Library, configured in `frontend/vitest.config.ts`.
- Assertions use Vitest `expect` plus `@testing-library/jest-dom`, initialized by `frontend/src/__tests__/setup.ts`.

**Run commands:**

```bash
cd frontend
npm test
npm run test:watch
npm run test:coverage
```

### Frontend browser

**Runner:**
- Playwright 1.61.x, configured in `frontend/playwright.config.ts`.
- Projects cover Chromium desktop (1280x800), mobile (390x844), and tablet (768x1024); package scripts expose desktop and mobile, while CI runs desktop and mobile smoke.

**Run commands:**

```bash
cd frontend
npm run test:e2e
npm run test:e2e:desktop
npx playwright test --project=chromium-tablet-768
```

### Agent service

**Runner:**
- Vitest 4.1.10 in Node environment, configured by `agent-service/vitest.config.ts`.
- Tests receive safe test values for required runtime configuration through the Vitest `env` block.

**Run commands:**

```bash
cd agent-service
npm test
npx vitest
npx tsc --noEmit
```

### CI policy scripts

- Repository-level policy tests use pytest in `tests/ci/` and dynamically load scripts from `scripts/ci/`.
- Run with `PYTHONPATH=. pytest tests/ci -q --tb=short` from the repository root, matching `.github/workflows/ci.yml`.

## Test File Organization

### Backend

**Location:**
- Legacy/coarse tests are directly under `backend/tests/`.
- Structured suites are grouped under `backend/tests/unit/`, `backend/tests/integration/`, `backend/tests/contract/`, `backend/tests/adversarial/`, `backend/tests/live/`, and `backend/tests/security/`.
- Shared data lives in `backend/tests/fixtures/`; shared async fixtures and collection gates live in `backend/tests/conftest.py`.

**Naming:**
- Files: `test_<behavior>.py`.
- Functions/methods: `test_<expected_behavior>`.
- Test classes group related policy cases without requiring `unittest.TestCase`, as in `backend/tests/test_test_policy.py`.

**Current inventory evidence:**
- 347 `test_*.py` files and 3,549 statically declared `test_` functions are present under `backend/tests/` at commit `b679b49`; parametrization can produce additional collected cases.
- A live `pytest --collect-only` was not available in the mapping environment because SQLAlchemy is not installed in the active Python interpreter.

### Frontend

**Location and naming:**
- Co-locate `*.test.ts` and `*.test.tsx` beside implementation under `frontend/src/`.
- Keep global setup/policy tests in `frontend/src/__tests__/`.
- Place browser journeys in `frontend/e2e/*.spec.ts`; shared Playwright utilities live in `frontend/e2e/helpers.ts`.

**Current inventory evidence:**
- `npx vitest list` collects 461 tests at commit `b679b49`.
- 28 Playwright spec files are present under `frontend/e2e/`.

### Agent service

**Location and naming:**
- Place runtime tests in `agent-service/tests/*.test.ts`.
- Mirror skill tests under `agent-service/tests/skills/`, for example `agent-service/tests/skills/answer-reading-question.test.ts`.
- Spike tests may live in `agent-service/spikes/**/*.test.mjs`, included by `agent-service/vitest.config.ts`.

**Current inventory evidence:**
- `npx vitest list` collects 1,039 cases at commit `b679b49`; table-driven loops in `agent-service/tests/policy-engine.test.ts` account for many generated cases.

## Test Classification and Timeouts

Backend tests must carry at least one primary marker from `unit`, `integration`, `contract`, or `live`. `e2e` is a secondary cross-layer marker and cannot stand alone. `backend/tests/conftest.py` fails collection for uncategorized tests and applies marker timeouts:

| Marker | Timeout | Intended scope |
|---|---:|---|
| `unit` | 5s | Isolated logic, in-memory fakes |
| `contract` | 15s | Schema, API, policy, compatibility |
| `integration` | 30s | Multiple components or real Postgres/Chroma |
| `live` | 180s | External model/service dependency |

`adversarial` and `e2e` supplement a primary marker. Two files, `backend/tests/unit/narrative_memory/test_builder_report.py` and `backend/tests/unit/narrative_memory/test_recovery.py`, contain no file-level marker text at HEAD; collection may inherit markers through package configuration only if explicitly provided elsewhere. Treat these as classification drift and verify collection after touching them.

Frontend Vitest uses a 5-second test timeout (`frontend/vitest.config.ts`). Playwright uses a 60-second test timeout, 15-second expectation/action timeouts, 30-second navigation timeout, one worker, and one CI retry (`frontend/playwright.config.ts`). Agent-service Vitest uses a 30-second timeout (`agent-service/vitest.config.ts`).

## Test Structure

### Backend suite pattern

```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


def test_low_coverage_fails() -> None:
    with pytest.raises(PolicyError, match="Coverage policy failed"):
        evaluate_coverage_report(policy, low_report)
```

Use a file-level `pytestmark` for the primary classification. Prefer arrange/act/assert through readable local values; use parametrization for input matrices and explicit assertions for persistence, error code, or lineage.

### Frontend component pattern

```typescript
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("Component", () => {
  it("renders the server state", () => {
    render(<Component value={fixture} />);
    expect(screen.getByText("expected")).toBeInTheDocument();
  });
});
```

Query by accessible role/name or visible text. Assert loading, error, empty, and success states for server-driven workspaces; `frontend/src/components/key-scenes/key-scenes.test.tsx` and `frontend/src/components/writing/markdown-editor.test.tsx` are strong references.

### Agent-service table pattern

```typescript
describe("decision matrix", () => {
  for (const rule of DOMAIN_ACTION_POLICY) {
    it(`${rule.action} preserves the global policy`, () => {
      expect(evaluate(rule.action, { sessionApprovals: EMPTY })).toBe(rule.policy);
    });
  }
});
```

Use table/matrix tests for frozen vocabularies and policy combinations. Add negative fail-closed cases and mirror tests that prove the registry/policy vocabulary cannot drift, following `agent-service/tests/policy-engine.test.ts` and `agent-service/tests/governance.test.ts`.

## Setup and Isolation

### Backend fixtures

- `backend/tests/conftest.py` creates an async in-memory SQLite engine, enables foreign keys, creates/drops metadata per `db_session`, and overrides FastAPI's `get_db` dependency for `httpx.AsyncClient`.
- This is the default unit/contract harness; integration tests that validate PostgreSQL or Chroma semantics run against locked services in `.github/workflows/ci.yml`.
- Always clear FastAPI dependency overrides during teardown. Use yielded async fixtures via `pytest_asyncio.fixture`.
- SQLite is not a substitute for dialect behavior. Place PostgreSQL generated-column, migration, full-text, and concurrency semantics under `backend/tests/integration/`.

### Frontend setup

- `frontend/src/__tests__/setup.ts` owns global jsdom/test-library setup.
- Reset mocks and browser globals in `beforeEach`/`afterEach` in the test that mutates them.
- Playwright starts the frontend and, by default, a backend on port 8010; it points the backend at the isolated CI database URL configured by `frontend/playwright.config.ts`.

### Agent-service setup

- `agent-service/vitest.config.ts` supplies a test-only gateway token and FastAPI URL so `agent-service/src/config.ts` can retain fail-fast startup validation.
- Pass dependency fakes to factories such as `createApp(deps)` rather than patching global modules where possible.

## Mocking

### Backend

**Framework:** pytest `monkeypatch`, `unittest.mock`, and in-memory fakes.

- Mock model gateways, external HTTP, clocks, and nondeterministic infrastructure at their narrow adapter boundary.
- Do not mock SQLAlchemy for persistence integration tests; use `db_session` or the CI Postgres service.
- Do not mock policy/canonicalization functions when they are the subject under test.

### Frontend

**Framework:** Vitest `vi.fn`, `vi.mock`, and React Testing Library.

- Mock network/API modules for component unit tests; assert calls and user-visible state.
- Do not mock the component under test or DOM behavior that jsdom supports.
- Browser success-path specs should use real frontend/backend routing where `frontend/playwright.config.ts` declares that contract; route mocks are appropriate only for explicit failure/isolation scenarios.

### Agent service

**Framework:** Vitest spies/fakes and dependency injection.

- Fake `fetch`, session/model adapters, and clocks at the edge.
- Keep policy, governance manifests, schema validation, normalization, and tool visibility real; those deterministic controls are security boundaries.

## Fixtures and Factories

**Backend:**
- Reusable policy fixtures live under `backend/tests/fixtures/`, including `coverage-policy-low.yml` and `coverage-policy-invalid.yml`.
- Domain/evaluation fixture data lives under `backend/evals/fixtures/`; tests should refer to versioned files and verify hashes/lineage where relevant.
- Use `tmp_path` for generated reports and CLI outputs, as in `tests/ci/test_release_gate.py`.

**Frontend:**
- Keep small typed objects in the test file; promote shared browser setup to `frontend/e2e/helpers.ts`.
- Avoid snapshotting large UI trees. Assert meaningful states, accessible controls, and emitted API payloads.

**Agent-service:**
- Construct inline manifest/policy records with helpers such as `rules(...)` in `agent-service/tests/policy-engine.test.ts`.
- Keep vendor archive integrity expectations in governance/lockfile tests rather than unpacking packages ad hoc.

## Coverage

### Declared policy

`.quality/coverage-policy.yml` is the canonical threshold source:

| Scope | Line | Branch |
|---|---:|---:|
| Backend overall | 80% | 70% |
| Backend critical | 90% | 85% |
| Frontend overall | 75% | 65% |
| Frontend critical | 85% | 75% |
| Changed lines | 90% | Not separately specified |

Critical globs cover backend auth/security/import/promotion/rollback and frontend hooks/stores/API modules. `backend/tests/test_test_policy.py` validates the YAML schema, locked values, glob matches, failure cases, timeouts, and flake policy. `frontend/src/__tests__/coverage-policy.test.ts` mirrors frontend threshold constants.

### Actual enforcement status

- `.github/workflows/ci.yml` runs backend coverage and uploads `backend/artifacts/backend-coverage.xml`.
- Frontend `npm run test:coverage` produces reports from `frontend/vitest.config.ts`, which intentionally has no `thresholds` block.
- No CI step parses the generated backend/frontend reports into `evaluate_coverage_report()` or calculates changed-line coverage. Repository search finds `evaluate_coverage_report()` only in `backend/tests/test_test_policy.py` and calls made by its synthetic unit cases.
- Therefore the policy definition is fail-closed under synthetic contract tests, but real CI coverage percentage and 90% diff coverage are not presently enforced. Do not describe the declared thresholds as active merge gates until a report adapter is wired into `.github/workflows/ci.yml`.

**View coverage:**

```bash
cd backend
pytest -m "unit or contract" --cov=app --cov-branch --cov-report=term-missing

cd frontend
npm run test:coverage
```

Agent-service has no coverage provider/configuration in `agent-service/package.json` or `agent-service/vitest.config.ts`.

## Test Types

**Unit tests:**
- Backend deterministic services/policies under `backend/tests/unit/` and marked legacy files under `backend/tests/`.
- Frontend utilities, hooks, stores, and components co-located in `frontend/src/`.
- Agent-service policy, governance, structured output, tools, and skills in `agent-service/tests/`.

**Contract tests:**
- Backend marker-based tests and `backend/tests/contract/` validate OpenAPI, schema, policy, and compatibility.
- `.github/workflows/ci.yml` exports OpenAPI and runs `oasdiff breaking` against `backend/openapi-baseline.json`.
- Root `tests/ci/` validates workflow safety, artifact policy, baseline promotion, branch protection, and the `ci-gate` aggregator.

**Integration tests:**
- `backend/tests/integration/` separates SQLite-compatible composition from real Postgres/Chroma qualification.
- CI starts digest-locked Postgres/Chroma services, runs Alembic checks, selected integration suites, and domain qualification scripts.

**E2E/browser tests:**
- `frontend/e2e/` runs browser journeys through Next.js and FastAPI.
- Playwright retains traces, screenshots, and videos on failure and emits HTML/JUnit reports.

**Live/evaluation tests:**
- `backend/tests/live/` requires external model dependencies and uses the 180-second marker timeout.
- Scheduled CI contains live/nightly quality producers with signed reports and controlled baseline promotion in `.github/workflows/ci.yml` and `.github/quality/baseline-policy.yml`.

**Security/adversarial tests:**
- Backend suites under `backend/tests/security/` and `backend/tests/adversarial/` complement Bandit, pip-audit, npm audit, CodeQL, and actionlint jobs.

## Quality Gates

The unified workflow `.github/workflows/ci.yml` is the active CI authority. `backend-ci.yml`, `frontend-ci.yml`, and `full-ci.yml` are disabled compatibility stubs.

| Producer/gate | Evidence |
|---|---|
| Static Python | Ruff check/format, Bandit, pip-audit in `.github/workflows/ci.yml` |
| Static frontend | TypeScript, ESLint, production dependency audit in `.github/workflows/ci.yml` |
| Unit/contract | Backend pytest coverage, frontend Vitest coverage, root CI policy pytest |
| API contract | OpenAPI export, `oasdiff`, contract pytest |
| Integration | Alembic plus locked Postgres/Chroma suites |
| Browser | Playwright desktop/mobile smoke with retained failure artifacts |
| Security | CodeQL for Python and JavaScript/TypeScript; actionlint |
| Aggregate | Fail-closed `scripts/ci/ci-gate.py`, exposed as the stable `ci-gate` context |

`scripts/ci/validate-workflow.py` and `tests/ci/test_workflow_security.py` enforce event/ref safety, timeouts, fork isolation, artifact retention, and secret-job restrictions. `.github/quality/baseline-policy.yml` governs nightly promotion and artifacts.

## Current Gaps and Prescriptive Guidance

- **Agent-service CI gap:** `agent-service/package.json` defines tests, lockfile verification, and package scanning, but `.github/workflows/ci.yml` never installs or tests agent-service. Until fixed, run `npm test`, `npx tsc --noEmit`, `npm run verify:lockfile`, and `npm run scan:packages` for every agent-service change.
- **Agent-service quality-tool gap:** no ESLint, formatter, coverage provider, or audit gate exists for `agent-service/`. Preserve local style and avoid claiming frontend gates cover this separate package.
- **Coverage enforcement gap:** wire actual XML/JSON coverage and changed-line calculations to the canonical `.quality/coverage-policy.yml`; synthetic evaluator tests alone do not enforce measured coverage.
- **Backend collection dependency:** test collection imports application dependencies from `backend/tests/conftest.py`. Install both `backend/requirements.txt` and `backend/requirements-dev.txt` before collection.
- **Documentation drift:** `backend/tests/README.md` and `frontend/src/__tests__/README.md` contain old test counts and incomplete coverage descriptions. Use runner collection and configs as authority.
- **Makefile gap:** root `make test` covers backend and frontend only; it excludes agent-service and `tests/ci/`. For cross-runtime changes, invoke all affected package commands explicitly.
- **Scripts gate gap:** Ruff CI covers only `backend/app` and `backend/tests`; changes under `backend/scripts/`, root `scripts/`, or `tests/ci/` need direct syntax/tests because the static job does not lint them.

## Verification Sequence for New Work

1. Run the smallest test file(s) that exercise the changed behavior.
2. Run the package type/lint gates: Ruff for backend, TypeScript + ESLint for frontend, TypeScript for agent-service.
3. Run the relevant marker or Vitest suite.
4. For API changes, export OpenAPI and run the contract/breaking checks.
5. For database/vector changes, run the locked-service integration suite.
6. For UI journeys, run the affected Playwright project and inspect retained artifacts on failure.
7. For CI/policy changes, run `PYTHONPATH=. pytest tests/ci -q --tb=short` plus `python scripts/ci/validate-workflow.py`.

---

*Testing analysis: 2026-08-07*
