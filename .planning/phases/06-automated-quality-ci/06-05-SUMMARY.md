# 06-05 Summary — OpenAPI Contracts, Frontend Consumer Tests, Playwright

**Status:** COMPLETE  
**Date:** 2026-07-12  
**Plan:** `.planning/phases/06-automated-quality-ci/06-05-PLAN.md`  
**Decisions:** D-07, D-12 (quality statuses + browser matrix)

## What Was Done

### Slice 1 — OpenAPI + frontend contracts

- `backend/scripts/export_openapi.py` — deterministic FastAPI OpenAPI export (`sort_keys`, indent=2)
- `backend/openapi-baseline.json` — frozen baseline of current app schema
- `backend/scripts/openapi_breaking.py` — oasdiff-first breaker with pure-Python fallback (path delete / type / required / auth / status)
- `backend/tests/contract/test_openapi_contract.py` — live vs baseline, nonbreaking pass, breaking fail
- Fixtures:
  - `tests/fixtures/openapi/nonbreaking.json` (additive only)
  - `tests/fixtures/openapi/breaking.json` (path remove, type change, required add, auth, status)
- Frontend:
  - `src/lib/api.ts` — `evalApi`, quality status catalog, deprecation types, comparable helpers
  - `src/hooks/use-eval.ts` + `src/stores/eval.ts` — durable quality job load/resume/cancel; null metrics when not comparable
  - `src/app/eval/page.tsx` — quality tab, status badges, deprecation banner
  - Tests: `api.contract.test.ts`, `page.test.tsx`, `use-eval.test.ts`, `eval.test.ts`

### Slice 2 — Playwright (desktop + 390px)

- Locked `@playwright/test@1.61.1`
- `frontend/playwright.config.ts`
  - projects: `chromium-desktop` (1280) + `chromium-mobile-390` (390×844)
  - browser timeout 60s; trace/screenshot/video on failure
  - webServer: uvicorn + next (default port 3005; CORS env for cookie origin gate)
- `e2e/core-flow.spec.ts` — register → upload → import → read → search → eval quality/runs
- `e2e/error-and-isolation.spec.ts` — wrong password, cross-user 404, API 503, blocked_dependency metrics=null

## Verification

```text
# OpenAPI
cd backend
venv\Scripts\python.exe scripts/export_openapi.py --output artifacts/openapi.json
oasdiff breaking openapi-baseline.json artifacts/openapi.json --fail-on ERR
# → No changes detected (exit 0)

oasdiff breaking openapi-baseline.json tests/fixtures/openapi/nonbreaking.json --fail-on ERR
# → exit 0

oasdiff breaking openapi-baseline.json tests/fixtures/openapi/breaking.json --fail-on ERR
# → exit 1 (5 ERR: path remove, required, type, status)

venv\Scripts\python.exe -m pytest tests/contract/test_openapi_contract.py -m contract
# → 7 passed

# Frontend unit/contract
cd frontend
npm run test:coverage
# → 54 passed (7 files)
# use-eval.ts lines 100%; stores/eval.ts ~72% lines / 92% funcs; api contract covered

# Playwright
npx playwright install chromium   # once
npx playwright test --project=chromium-desktop --project=chromium-mobile-390 --retries=0
# → 10 passed (52.8s)
```

## Tooling

| Tool | Version / status |
|------|------------------|
| oasdiff | v1.17.0 via `go install github.com/oasdiff/oasdiff@v1.17.0` (GOSUMDB=sum.golang.org) |
| Go | 1.26.5 (installed during plan for oasdiff) |
| @playwright/test | 1.61.1 |
| Python breaker | `scripts/openapi_breaking.py` fallback when oasdiff missing |

## Deviations / notes

1. **Default E2E port 3005** — Windows host returned `EACCES` on `:3000`; config defaults to 3005 and injects `NOVELMIND_CORS_ORIGINS` so cookie CSRF origin checks accept the Playwright origin.
2. **Upload response id = ImportJob.id** — dialog polls `/novels/{jobId}/import-status` which can 404; core flow asserts import via bookshelf card after background job completes (job itself succeeds).
3. **Next.js Turbopack** may log “Persisting failed: Access denied” on this machine; does not fail tests.
4. **passlib/bcrypt** logs a trapped `__about__` warning; auth still works.

## Files Changed (06-05 scope)

| Path | Role |
|------|------|
| `backend/scripts/export_openapi.py` | OpenAPI export |
| `backend/scripts/openapi_breaking.py` | Breaking detector + oasdiff wrapper |
| `backend/openapi-baseline.json` | Frozen baseline |
| `backend/tests/contract/*` | Contract tests |
| `backend/tests/fixtures/openapi/*` | pos/neg fixtures |
| `frontend/src/lib/api.ts` + contract test | Eval/quality consumer |
| `frontend/src/hooks/use-eval.ts` + test | Hook |
| `frontend/src/stores/eval.ts` + test | Store |
| `frontend/src/app/eval/page.tsx` + test | Quality UI |
| `frontend/playwright.config.ts` | Desktop + 390px matrix |
| `frontend/e2e/*` | Core + error/isolation journeys |
| `frontend/package.json` + lock | Playwright 1.61.1 |

## Out of Scope (confirmed)

- CI DAG / artifacts upload (06-06)
- Branch protection / ci-gate aggregate (06-07)
- Fixing upload response job-id vs novel-id product bug (documented only)

## Next

- Do **not** start 06-06 from this plan unless scheduled.
- 06-06 consumes OpenAPI artifacts, Playwright failure traces, and JUnit/coverage for unified CI.
