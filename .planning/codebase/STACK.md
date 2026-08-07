---
last_mapped_commit: b679b49
---

# Technology Stack

**Analysis Date:** 2026-08-07

## Languages

**Primary:**
- Python 3.11-3.13 - FastAPI 后端、迁移、质量评测与仓库级 CI 策略脚本；容器和统一 CI 均以 Python 3.12 为基线（`backend/app/`, `backend/migrations/`, `backend/Dockerfile`, `.github/workflows/ci.yml`, `README.md`）。
- TypeScript 5.x - Next.js 客户端与独立 agent runtime；前端声明 `^5.5.0`，agent-service 精确固定 `5.9.3`（`frontend/package.json`, `frontend/src/`, `agent-service/package.json`, `agent-service/src/`）。

**Secondary:**
- JavaScript / ESM - Next.js 配置、agent-service 启动入口和依赖治理脚本（`frontend/next.config.mjs`, `frontend/eslint.config.mjs`, `agent-service/start.mjs`, `agent-service/scripts/*.mjs`）。
- SQL / Alembic DDL - PostgreSQL schema 演进（`backend/migrations/`, `backend/alembic.ini`）。
- YAML / JSON - CI、质量策略、服务锁、技能契约和部署配置（`.github/workflows/ci.yml`, `.quality/coverage-policy.yml`, `.github/ci/service-lock.json`, `agent-service/src/skills/*/skill.yaml`, `deploy/cloudflare/config.novelmind-win.yml`）。
- PowerShell / Make - Windows 本地保活、Cloudflare Tunnel 和跨服务开发命令（`scripts/keep-alive.ps1`, `deploy/cloudflare/*.ps1`, `Makefile`）。

## Runtime

**Environment:**
- Backend: CPython 3.12 in Docker and CI; maintainers document supported Python 3.11-3.13 (`backend/Dockerfile`, `.github/workflows/ci.yml`, `README.md`).
- Frontend: Node.js 20 in Docker/CI; the repository documents Node 20.9+ (`frontend/Dockerfile`, `.github/workflows/ci.yml`, `README.md`).
- Agent service: Node.js `>=22.19.0`, ESM, target ES2023; this is a separate runtime requirement from the frontend Node 20 baseline (`agent-service/package.json`, `agent-service/tsconfig.json`).
- Infrastructure: Docker Compose provides PostgreSQL/pgvector, ChromaDB and an optional Neo4j profile for development (`docker-compose.yml`).

**Package Manager:**
- Python: pip with split runtime/dev manifests; no Python lockfile is present (`backend/requirements.txt`, `backend/requirements-dev.txt`, `backend/Dockerfile`).
- Frontend: npm with lockfileVersion 3; use `npm ci` in CI and treat `frontend/package-lock.json` as the resolved dependency authority (`frontend/package.json`, `frontend/package-lock.json`, `.github/workflows/ci.yml`).
- Agent service: npm with lockfileVersion 3 plus an application-owned governance lock; install runtime packages only from the exact pins accepted in both lockfiles (`agent-service/package.json`, `agent-service/package-lock.json`, `agent-service/packages.lock.json`).

## Frameworks

**Core:**
- FastAPI `>=0.115` + Uvicorn `>=0.32` - ASGI API and application lifecycle (`backend/requirements.txt`, `backend/app/main.py`).
- SQLAlchemy `>=2.0` + asyncpg `>=0.30` - async ORM/session layer over PostgreSQL (`backend/requirements.txt`, `backend/app/core/database.py`).
- Pydantic `>=2.13` + pydantic-settings `>=2.8` - request contracts and environment-backed configuration (`backend/requirements.txt`, `backend/app/config.py`, `backend/app/schemas/`).
- Next.js `16.3.0-canary.6` + React/React DOM `19.2.7` - App Router web client (`frontend/package.json`, `frontend/src/app/`).
- Native `node:http` + Earendil Pi `0.83.0` packages - standalone SSE agent runtime, model facade, tool execution and governed skill loading (`agent-service/package.json`, `agent-service/src/server.ts`, `agent-service/src/agent/provider.ts`).
- Tailwind CSS `^3.4.0`, Base UI `^1.5.0`, shadcn `^4.10.0` - frontend styling/component primitives (`frontend/package.json`, `frontend/tailwind.config.ts`, `frontend/src/components/`).

**Testing:**
- pytest `>=8.3`, pytest-asyncio `>=0.24`, pytest-cov `7.1.0`, pytest-timeout `2.4.0` - backend unit, contract, integration, adversarial and live suites (`backend/requirements-dev.txt`, `backend/pytest.ini`, `backend/tests/`).
- Vitest `4.1.10` + Testing Library + jsdom - frontend tests and agent-service Node tests (`frontend/package.json`, `frontend/vitest.config.ts`, `agent-service/package.json`, `agent-service/vitest.config.ts`).
- Playwright `^1.61.1` - desktop and 390px mobile browser E2E (`frontend/package.json`, `frontend/playwright.config.ts`, `frontend/e2e/`).

**Build/Dev:**
- Next.js build, TypeScript no-emit checks, ESLint 9.39.1 and PostCSS/Tailwind form the frontend toolchain (`frontend/package.json`, `frontend/eslint.config.mjs`, `frontend/postcss.config.mjs`).
- TypeScript compiler emits agent-service to `agent-service/dist/`; `agent-service/start.mjs` requires that compiled output (`agent-service/tsconfig.json`, `agent-service/start.mjs`).
- Alembic owns database migrations; use migrations rather than `Base.metadata.create_all` outside local development (`backend/alembic.ini`, `backend/migrations/`, `backend/app/core/database.py`).
- Root Make targets and `scripts/keep-alive.ps1` coordinate local ports 8010/3005/3100; ZCodeProxy remains separately managed on 3001 (`Makefile`, `scripts/keep-alive.ps1`, `README.md`).

## Key Dependencies

**Critical:**
- `litellm>=1.83.10` - common OpenAI/Anthropic/Gemini-compatible chat and embedding facade; Vertex traffic also has a direct Google REST path (`backend/requirements.txt`, `backend/app/services/ai_service.py`, `backend/app/services/vertex_gemini.py`).
- `chromadb==1.5.9` + `pgvector>=0.3` - vector retrieval clients; Chroma is deliberately exact-pinned to the CI service image (`backend/requirements.txt`, `backend/app/services/vector_store.py`, `.github/ci/service-lock.json`).
- `sentence-transformers>=3.0` - default local BGE embeddings, with Torch supplied transitively (`backend/requirements.txt`, `backend/app/services/ai_service.py`).
- `cryptography>=42.0`, `bcrypt>=4.0,<5.0`, `pyjwt>=2.8` - API-key encryption, password hashing and custom JWT sessions (`backend/requirements.txt`, `backend/app/core/crypto.py`, `backend/app/core/security.py`).
- `@tanstack/react-query^5.50.0`, `zustand^4.5.0`, `axios^1.7.0` - server state, client state and HTTP client (`frontend/package.json`, `frontend/src/lib/api.ts`, `frontend/src/stores/`).
- `cytoscape@3.34.0`, `echarts^6.1.0`, `echarts-for-react^3.0.2` - relationship and quality/timeline visualizations (`frontend/package.json`, `frontend/src/components/relationships/relationship-graph.tsx`, `frontend/src/components/timeline/timeline-chart.tsx`).
- Earendil Pi packages `0.83.0`, `pi-mcp-adapter 2.17.0`, AJV `8.20.0`, TypeBox `1.3.7` - agent session runtime, MCP boundary and schema validation (`agent-service/package.json`, `agent-service/packages.lock.json`).

**Infrastructure:**
- PostgreSQL 16 + pgvector is the authoritative transactional store; CI pins PostgreSQL 16.10 by digest (`docker-compose.yml`, `.github/ci/service-lock.json`, `docker-compose.ci.yml`).
- ChromaDB is a disposable vector projection; CI pins server/client 1.5.9, while development Compose uses `latest` (`backend/app/services/vector_store.py`, `.github/ci/service-lock.json`, `docker-compose.yml`).
- Neo4j 5 Community is an optional development projection only; no runtime Neo4j driver is installed and adapters fail closed (`docker-compose.yml`, `backend/app/services/relationships/projection.py`, `backend/app/services/knowledge/graph_sync.py`).

## Configuration

**Environment:**
- Backend configuration authority is the frozen `Settings` singleton. It reads `.env` plus `NOVELMIND_`-prefixed variables, with explicit aliases where double-prefixing would occur (`backend/app/config.py`).
- Persisted runtime preference authority is PostgreSQL `app_settings`; currently only `routing_preference` is managed there and synchronized into the in-process router (`backend/app/models/app_setting.py`, `backend/app/services/settings_service.py`, `backend/app/main.py`).
- User AI model configurations are PostgreSQL records; provider keys are Fernet-encrypted and omitted from GET responses (`backend/app/models/ai_model.py`, `backend/app/core/crypto.py`, `backend/app/api/models.py`).
- Frontend API routing authority is split: browser calls default to `/api`, while Next rewrites read `BACKEND_URL` and `AGENT_SERVICE_URL` (`frontend/src/lib/api.ts`, `frontend/next.config.mjs`).
- Agent-service configuration is frozen at module load. `NOVELMIND_GATEWAY_TOKEN` is required and startup fails immediately when absent; `FASTAPI_BASE_URL`, `PORT` and poller settings define the service boundary (`agent-service/src/config.ts`).
- Environment files exist at `backend/.env`, `backend/.env.example`, and `frontend/.env.local`; their contents are outside codebase-map scope and must remain unquoted (`backend/.env`, `backend/.env.example`, `frontend/.env.local`).

**Build:**
- Frontend authority: `frontend/package.json`, `frontend/package-lock.json`, `frontend/tsconfig.json`, `frontend/next.config.mjs`, `frontend/eslint.config.mjs`, `frontend/vitest.config.ts`, `frontend/playwright.config.ts`.
- Agent-service authority: `agent-service/package.json`, `agent-service/package-lock.json`, `agent-service/packages.lock.json`, `agent-service/tsconfig.json`, `agent-service/vitest.config.ts`.
- Backend authority: `backend/requirements.txt`, `backend/requirements-dev.txt`, `backend/ruff.toml`, `backend/pytest.ini`, `backend/alembic.ini`, `backend/Dockerfile`.
- Cross-repository authority: `Makefile`, `.github/workflows/ci.yml`, `.quality/coverage-policy.yml`, `.github/quality/baseline-policy.yml`, `.github/ci/service-lock.json`.

## Dependency & Governance Ownership

- Unified CI authority is `.github/workflows/ci.yml`; legacy backend/frontend/full workflows are disabled `workflow_call` stubs and must not regain independent triggers (`.github/workflows/backend-ci.yml`, `.github/workflows/frontend-ci.yml`, `.github/workflows/full-ci.yml`).
- The stable branch-protection context is `ci-gate`; producer results are aggregated fail-closed, with event/fork/secret isolation enforced by policy code (`.github/workflows/ci.yml`, `scripts/ci/ci-gate.py`, `scripts/ci/validate-workflow.py`).
- Coverage thresholds and exact test-tool versions are policy-owned, not local developer preferences (`.quality/coverage-policy.yml`, `backend/requirements-dev.txt`, `frontend/vitest.config.ts`).
- Runtime service versions are governed by SHA-256 image digests in `.github/ci/service-lock.json`, validated before integration jobs use `docker-compose.ci.yml` (`.github/workflows/ci.yml`, `scripts/ci/validate-workflow.py`).
- Frontend dependency exceptions belong in `overrides`; current overrides force patched Sharp/PostCSS/Hono families and therefore require lockfile regeneration and CI audit together (`frontend/package.json`, `frontend/package-lock.json`, `.github/workflows/ci.yml`).
- Agent dependency admission is stricter: exact package pins, integrity, adopt/fork/reject verdict, qualification report and permission manifest must agree across both locks (`agent-service/package.json`, `agent-service/package-lock.json`, `agent-service/packages.lock.json`, `agent-service/scripts/verify-lockfile.mjs`).
- Agent startup repeats governance checks before binding its listener, including permission manifests, tool-name collisions and optional schema drift checks (`agent-service/src/server.ts`, `agent-service/src/governance/lockfile.ts`, `agent-service/src/governance/permission-manifest.ts`).
- Dynamic package installation/update is forbidden; lifecycle scripts are installed with `--ignore-scripts` and separately checked against a narrow audit allowlist (`agent-service/scripts/scan-packages.mjs`, `agent-service/qualification/`).
- Python dependency ownership is weaker: most runtime packages use open-ended minimum constraints and there is no resolved lock, so the installation time determines the actual closure (`backend/requirements.txt`, `backend/requirements-dev.txt`, `backend/Dockerfile`, `.github/workflows/ci.yml`).

## Platform Requirements

**Development:**
- Use Python 3.11-3.13, Node 20.9+ for frontend, Node >=22.19 for agent-service, npm, and Docker Desktop (`README.md`, `agent-service/package.json`).
- Start PostgreSQL/Chroma through Compose, run Alembic, then run FastAPI on 8010, Next.js on 3005 and agent-service on 3100 (`README.md`, `Makefile`, `scripts/keep-alive.ps1`).
- Local BGE embeddings require the configured model directory and adequate RAM; CUDA is optional because the service falls back to CPU (`backend/app/config.py`, `backend/app/services/ai_service.py`).

**Production:**
- No supported production deployment target is defined; the deployment guide explicitly classifies the repository as local-development/security-baseline only (`docs/DEPLOYMENT.md`).
- `backend/Dockerfile` runs Uvicorn but `frontend/Dockerfile` runs `next dev`; root Compose contains only data services, not application services (`backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml`).
- Cloudflare Tunnel configuration exposes the Windows development frontend/API, not a repeatable production platform (`deploy/cloudflare/config.novelmind-win.yml`, `deploy/cloudflare/README.md`).

## Boundary Risks

- Agent-service requires Node >=22.19, but repository CI declares Node 20 and contains no agent-service install/test/governance job. Changes under `agent-service/` can therefore bypass the unified required check (`agent-service/package.json`, `.github/workflows/ci.yml`).
- Backend dependency resolution is non-reproducible because lower bounds are re-resolved on each install; only a few packages are exact-pinned (`backend/requirements.txt`, `backend/requirements-dev.txt`).
- Development Chroma uses `latest` while application/CI client is exact `1.5.9`; local behavior can drift independently from the qualified service (`docker-compose.yml`, `backend/requirements.txt`, `.github/ci/service-lock.json`).
- Chroma CI port authority is inconsistent: workflow metadata/commentary says 8002, while Compose, service lock and integration fixture default use 8001 (`.github/workflows/ci.yml`, `docker-compose.ci.yml`, `.github/ci/service-lock.json`, `backend/tests/integration/conftest.py`).
- Frontend and backend Dockerfiles do not form the topology described by local ports: frontend uses development mode and Node 20, backend exposes 8000, while host tooling standardizes 3005/8010 (`frontend/Dockerfile`, `backend/Dockerfile`, `Makefile`, `docs/DEPLOYMENT.md`).
- Runtime model authority is intentionally split between environment defaults, encrypted per-user model records and the persisted routing preference. New model-routing work must preserve which layer owns endpoint/key/model/tier decisions (`backend/app/config.py`, `backend/app/models/ai_model.py`, `backend/app/services/settings_service.py`, `backend/app/api/gateway.py`).

---

*Stack analysis: 2026-08-07*
