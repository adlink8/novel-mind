---
last_mapped_commit: b679b49
---

# Coding Conventions

**Analysis Date:** 2026-08-07

## Scope and Authority

- Treat `AGENTS.md` as the repository-wide contribution contract and the nearest module `README.md` as the local boundary guide.
- Use `Makefile` for the supported backend/frontend developer commands. Agent-service commands are package-local in `agent-service/package.json`.
- Keep framework-specific differences: Python/FastAPI uses Ruff and pytest; Next.js uses ESLint, TypeScript, Vitest, and Playwright; the Node agent runtime uses strict TypeScript and Vitest.
- Do not copy conventions between runtimes mechanically. In particular, `.js` suffixes in TypeScript imports are required by `agent-service/tsconfig.json` (`NodeNext`) but not by `frontend/tsconfig.json` (`moduleResolution: bundler`).

## Naming Patterns

### Backend (`backend/`)

**Files:**
- Use `snake_case.py` for modules and `test_<subject>.py` for tests, as in `backend/app/services/import_service.py` and `backend/tests/test_import_job.py`.
- Split large domains into packages with responsibility-named modules, as in `backend/app/services/timeline/extraction.py`, `backend/app/services/timeline/promotion.py`, and `backend/app/services/timeline/reconcile.py`.
- CLI entry files use imperative `snake_case` names such as `backend/scripts/run_rag_quality.py` and `backend/scripts/reconcile_narrative_unit_index.py`.

**Functions and variables:**
- Use `snake_case`; private helpers start with `_`, as in `_resolve_timeout()` in `backend/tests/conftest.py` and `_fail()` in `scripts/check_phase_execution_gate.py`.
- Use uppercase module constants for fixed policy and vocabulary values, such as `PRIMARY_MARKERS` and `MARKER_TIMEOUTS` in `backend/tests/conftest.py`.

**Types:**
- Use `PascalCase` for classes, Pydantic models, dataclasses, and custom exceptions.
- Prefer built-in generics and PEP 604 unions (`list[str]`, `dict[str, Any]`, `str | None`) as demonstrated by `backend/tests/test_test_policy.py`.

### Frontend (`frontend/`)

**Files:**
- Use kebab-case for components and utilities (`frontend/src/components/app-theme-sync.tsx`, `frontend/src/lib/reader-selection.ts`).
- Co-locate unit tests with the subject using `.test.ts` or `.test.tsx`; browser tests use `.spec.ts` under `frontend/e2e/`.
- Next.js routes retain framework names such as `frontend/src/app/eval/page.tsx`.

**Functions and variables:**
- Use `camelCase`; hooks begin with `use`, as in `frontend/src/hooks/use-novels.ts`.
- React components, exported domain types, and interfaces use `PascalCase`.
- Zustand stores use `use<Domain>Store`, as in `frontend/src/stores/novelStore.ts`.

### Agent service (`agent-service/`)

**Files:**
- Use kebab-case modules and `.test.ts` tests, for example `agent-service/src/structured-output/cited-answer-builder.ts` and `agent-service/tests/cited-answer-builder.test.ts`.
- Import local TypeScript modules with emitted `.js` suffixes, as in `agent-service/tests/policy-engine.test.ts`; this is intentional NodeNext behavior.

**Functions and types:**
- Use `camelCase` functions and `PascalCase` types/classes.
- Represent untrusted boundaries as `unknown`, then narrow or validate before use; examples include `agent-service/src/structured-output/validator.ts` and `agent-service/src/governance/lockfile.ts`.
- Prefer discriminated unions for protocol events, as in `agent-service/src/transport/sse.ts`.

### Repository scripts (`scripts/` and `scripts/ci/`)

- Python script names use kebab-case in the CI tool set (`scripts/ci/ci-gate.py`, `scripts/ci/validate-workflow.py`) and snake_case in root execution gates (`scripts/check_phase_execution_gate.py`). This is existing drift; follow the convention of the directory being modified.
- CLI entry points use `main(argv: list[str] | None = None) -> int` and terminate with `raise SystemExit(main())`, as in `scripts/ci/ci-gate.py` and `scripts/check_phase_execution_gate.py`.

## Code Style

### Formatting

- Backend formatting is authoritative through Ruff: run `ruff format --check app tests`. `backend/ruff.toml` does not override formatter defaults, so Ruff's default 88-character target applies.
- Backend lint rules are deliberately narrow and version-stable: `backend/ruff.toml` selects only `E4`, `E7`, `E9`, and `F`; tests ignore `E402` because `pytestmark` may sit between pytest and subject imports.
- Frontend uses ESLint 9 with `eslint-config-next/core-web-vitals` from `frontend/eslint.config.mjs`. No Prettier configuration is present; preserve the local double-quote, semicolon, and trailing-comma style instead of applying an unrelated formatter.
- Agent-service has no ESLint or Prettier configuration and no format script in `agent-service/package.json`. Preserve adjacent formatting; TypeScript compilation is the only configured static syntax/type check.
- Root and backend scripts are not included in `Makefile`'s Ruff targets or the CI `ruff check app tests` command in `.github/workflows/ci.yml`; format them consistently with nearby scripts and validate them directly when changed.

### Linting and typing

- Backend: run `cd backend && ruff check app tests` and `ruff format --check app tests`. No mypy or Pyright configuration is present, so annotations document contracts but are not statically enforced.
- Frontend: `frontend/tsconfig.json` enables `strict`, `noEmit`, and path alias `@/* -> ./src/*`; run `npx tsc --noEmit` plus `npm run lint`.
- Agent-service: `agent-service/tsconfig.json` enables `strict`, `forceConsistentCasingInFileNames`, and NodeNext resolution. Run `npx tsc --noEmit`; this command is not currently exposed as a package script or CI producer.
- Do not add broad suppressions. Existing suppressions are localized, such as `# noqa: BLE001` with a reason in `backend/app/api/gateway.py`; new suppressions should carry an explanation.

## Import Organization

### Backend order

1. `from __future__ import annotations` where the module uses postponed annotations.
2. Standard library imports.
3. Third-party imports.
4. `app.*` imports.

Use this grouping in new backend modules. `backend/tests/conftest.py` contains legacy ordering drift (SQLAlchemy imports and `import app.models` placement); do not reproduce it.

### Frontend order

1. React/Next and third-party packages.
2. `@/` aliased application imports.
3. Relative imports and styles.

Use `@/` for cross-directory code and relative imports for nearby test subjects. The alias is defined consistently in `frontend/tsconfig.json` and `frontend/vitest.config.ts`.

### Agent-service order

1. Node built-ins using `node:` specifiers.
2. External packages.
3. Relative runtime modules with `.js` suffixes.
4. Type-only dependencies using `import type` where applicable.

## Error Handling

### Backend

- Raise domain-specific exceptions inside services, then map them to stable HTTP status/detail shapes in routers. `_map_error()` helpers in `backend/app/api/derivative_context.py` and `backend/app/api/derivative_revisions.py` are the preferred pattern.
- Raise `HTTPException` directly for request ownership, validation, not-found, and conflict boundaries in API modules such as `backend/app/api/auth.py`.
- Preserve exception chaining (`raise ... from exc`) when translating errors.
- Catch broad `Exception` only at an explicit process/API boundary, log context, and return a sanitized stable error. Broad catches in `backend/app/api/analysis.py` and `backend/app/api/clues.py` expose truncated/raw exception strings and are drift from the safer mapper pattern.
- Use `logging.getLogger(__name__)` in application modules. `print()` is acceptable for CLI status/output in `backend/scripts/run_rag_quality.py`; the `print()` in `backend/app/services/ai_service.py` is library-layer drift.

### Frontend

- Treat caught values as unknown and narrow with `err instanceof Error`, as in `frontend/src/stores/novelStore.ts` and `frontend/src/stores/eval.ts`.
- Store or render a stable fallback message; do not silently convert failures to successful empty state.
- API helpers in `frontend/src/lib/api.ts` own transport normalization; components should consume typed results rather than duplicate Axios parsing.
- `console.error` is currently used in UI event handlers such as `frontend/src/app/eval/page.tsx`; keep it for diagnostics only and surface a user-visible state as well.

### Agent service

- Fail closed for configuration, governance, policy, schema, and tool-registry violations. Examples are `agent-service/src/config.ts`, `agent-service/src/governance/lockfile.ts`, and `agent-service/src/policy/engine.ts`.
- Use typed errors with stable codes at transport boundaries; `AgentToolError` in `agent-service/src/tools/fastapi-client.ts` and `PolicyDenied` in `agent-service/src/policy/engine.ts` are reference patterns.
- Convert terminal runtime failures into stable SSE `run_end` events in `agent-service/src/server.ts`; never leak gateway tokens or raw credentials.
- Process termination is reserved for startup governance failure in `startServer()` (`agent-service/src/server.ts`), not ordinary request errors.

## Logging

**Backend framework:** Python `logging`.

- Create a module logger with `logging.getLogger(__name__)` and include identifiers/context without secrets.
- CLI scripts may write stable `[OK]`, `[INFO]`, `[FAIL]`, or gate-prefixed messages to stdout/stderr, as in `backend/scripts/run_rag_quality.py` and `scripts/check_phase_execution_gate.py`.

**TypeScript runtimes:** `console`.

- Frontend console calls are diagnostic; user-facing errors belong in component/store state.
- Agent-service logs startup/governance failures with a service prefix. Never log `NOVELMIND_GATEWAY_TOKEN`; `agent-service/src/config.ts` explicitly treats it as secret.

## Comments and Documentation

- Module docstrings in Python should explain non-obvious responsibility or policy, not restate the filename. Policy-heavy examples are `backend/tests/test_test_policy.py` and `scripts/ci/validate-workflow.py`.
- Use comments to preserve locked decisions, fail-closed rationale, and framework constraints. `backend/ruff.toml`, `frontend/vitest.config.ts`, and `.github/workflows/ci.yml` use this style.
- TSDoc/JSDoc is selective: document exported protocol and governance functions, as in `agent-service/src/server.ts`; ordinary local functions do not require boilerplate comments.
- Chinese domain comments and English identifiers coexist intentionally. Match the surrounding module language.

## Function Design

**Backend:**
- Keep route functions focused on dependency extraction, validation, service invocation, and exception mapping.
- Put durable domain logic in `backend/app/services/`; use small private helpers for normalization and policy checks.
- Annotate public and test helper signatures. Existing older API/service modules are not uniformly annotated, so typing is a direction for touched code rather than a repository-wide proven invariant.

**Frontend:**
- Keep network access in `frontend/src/lib/`, remote-state composition in `frontend/src/hooks/`, client state in `frontend/src/stores/`, and rendering in `frontend/src/components/` or route files.
- Hooks and stores return typed domain state. Avoid `any`; use `unknown` plus narrowing at dynamic boundaries.

**Agent-service:**
- Inject I/O dependencies for tests through dependency objects, following `createApp(deps)` in `agent-service/src/server.ts`.
- Prefer pure deterministic helpers for policy, canonicalization, and validation; test tables exhaustively as in `agent-service/tests/policy-engine.test.ts`.

## Module Design

**Exports:**
- Backend services commonly expose module-level service objects, but newer domain packages favor focused functions/classes. Follow the neighboring package rather than introducing a second style.
- Frontend modules use named exports for reusable components, hooks, types, and helpers; Next.js route files use required default exports.
- Agent-service uses named ESM exports and explicit `.js` local imports.

**Barrel files:**
- Python `__init__.py` files register/export selected package surfaces, including `backend/app/models/__init__.py`.
- TypeScript barrel files are not a dominant pattern; import from the owning module to retain clear boundaries.

## Intentional Differences vs. Drift

| Area | Intentional framework difference | Current drift to avoid extending |
|---|---|---|
| Module resolution | Frontend `@/` bundler alias vs. agent-service NodeNext `.js` imports (`frontend/tsconfig.json`, `agent-service/tsconfig.json`) | Agent-service lacks an enforced lint/format command (`agent-service/package.json`) |
| Formatting | Ruff for Python and Next ESLint for frontend (`backend/ruff.toml`, `frontend/eslint.config.mjs`) | No shared formatter for either TS package; preserve local style |
| Typing | Strict TypeScript in both TS packages; runtime validation for untrusted agent payloads | Python annotations are not checked by mypy/Pyright; scripts are outside Ruff CI scope |
| Errors | HTTP exceptions in FastAPI, stateful UI errors in React, coded/fail-closed errors in agent runtime | Raw/truncated exception details in `backend/app/api/analysis.py`; library `print()` in `backend/app/services/ai_service.py` |
| CI | Unified backend/frontend quality DAG in `.github/workflows/ci.yml` | Agent-service tests/typecheck/audit are absent from that DAG |

---

*Convention analysis: 2026-08-07*
