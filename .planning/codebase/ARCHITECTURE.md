---
last_mapped_commit: b679b49
---
<!-- refreshed: 2026-08-07 -->
# Architecture

**Analysis Date:** 2026-08-07

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Browser / Next.js App Router                                             │
│ `frontend/src/app` + `frontend/src/components`                           │
└───────────────────────┬───────────────────────────┬──────────────────────┘
                        │ `/api/*` REST             │ `/agent/*` POST/SSE
                        │ Next rewrite              │ Next rewrite
                        ▼                           ▼
┌───────────────────────────────────────┐  ┌───────────────────────────────┐
│ FastAPI authority                     │◄─┤ Node Agent Service            │
│ `backend/app/main.py`                 │  │ `agent-service/src/server.ts` │
│                                       │  │ `agent-service/src/poller.ts` │
│ API → services → ORM/contracts        │  │ Pi sessions, skills, tools    │
└──────────────┬───────────┬────────────┘  └──────────────┬────────────────┘
               │           │                              │
               ▼           ▼                              │ HTTP callbacks
┌────────────────────┐  ┌────────────────────┐             │ `/api/agent*`
│ PostgreSQL          │  │ ChromaDB HTTP      │◄────────────┘
│ ORM + durable truth │  │ vector indexes     │
│ `backend/app/models`│  │ `vector_store.py`  │
└────────────────────┘  └────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ Files and generated assets                                               │
│ `backend/uploads`, `backend/storage`, `backend/artifacts`, `backend/evals`│
└──────────────────────────────────────────────────────────────────────────┘
```

The deployed shape is a three-runtime modular monolith: Next.js owns presentation, FastAPI owns authentication, authorization, durable state, domain rules, and finalization, while the Node Agent Service owns Pi session execution and SSE streaming. `frontend/next.config.mjs` is the browser-side junction: `/api/:path*` targets FastAPI and `/agent/:path*` targets Agent Service.

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Next.js application | Route composition, authentication gate, workspaces, reader, editor, and browser state | `frontend/src/app/layout.tsx`, `frontend/src/app` |
| Frontend API boundary | Axios REST client, browser JWT attachment, manually mirrored response/request types | `frontend/src/lib/api.ts`, `frontend/src/lib/*-api.ts` |
| Frontend agent stream | Authenticated POST/SSE parsing and cancellation | `frontend/src/lib/sse.ts` |
| FastAPI composition root | Lifespan, middleware, global errors, and all router registration | `backend/app/main.py` |
| HTTP route layer | Authentication/ownership dependencies, request validation, orchestration, HTTP mapping | `backend/app/api` |
| Domain/service layer | Import, retrieval, analysis, knowledge, visual, derivative, export, and agent runtime rules | `backend/app/services` |
| Persistence model layer | SQLAlchemy metadata and durable domain state | `backend/app/models` |
| Python contract layer | Pydantic request/response and internal frozen contracts | `backend/app/schemas` |
| Agent Service HTTP runtime | Direct SSE runs, governance startup gates, approval loop, and finalization callback | `agent-service/src/server.ts` |
| Agent Service poller | Pull-based execution of queued `chat_backfill` runs | `agent-service/src/poller.ts` |
| Agent tool adapter | TypeBox tool definitions and HTTP forwarding into owner-scoped FastAPI tools | `agent-service/src/tools/registry.ts`, `agent-service/src/tools/fastapi-client.ts` |
| Skill runtime | Versioned skill manifests, JSON schemas, instructions, loader validation | `agent-service/src/skills`, `agent-service/src/skills/loader.ts` |
| Vector index | Per-novel and named Chroma collections over HTTP | `backend/app/services/vector_store.py` |
| Database/migrations | Async SQLAlchemy sessions and Alembic schema evolution | `backend/app/core/database.py`, `backend/migrations/versions` |

## Pattern Overview

**Overall:** Three-runtime modular monolith with a layered FastAPI core and an authority-preserving Agent sidecar.

**Key Characteristics:**
- Keep durable authority in FastAPI/PostgreSQL. Agent Service does not connect to the database; it obtains per-run authority and calls FastAPI through `agent-service/src/tools/fastapi-client.ts`.
- Treat browser REST and agent streaming as separate transports. `frontend/src/lib/api.ts` targets `/api`, while `frontend/src/lib/sse.ts` targets `/agent` through `frontend/next.config.mjs`.
- Organize backend domain logic as service packages under `backend/app/services/<domain>`; route modules under `backend/app/api` should coordinate rather than own algorithms.
- Use candidate → validation/review → explicit publication flows for knowledge, visual, and derivative domains. The relevant state machines live in `backend/app/services/agent_runtime`, `backend/app/services/visual_bible`, `backend/app/services/derivative_*`, and their ORM models.
- Preserve owner, novel, cutoff, branch, source-version, and evidence lineage at every boundary. The strongest enforcement points are `backend/app/api/dependencies.py`, `backend/app/services/agent_tools/facade.py`, and `backend/app/services/agent_runtime/structured_output_integrity.py`.

## Layers

**Presentation and Route Layer:**
- Purpose: Render product routes and coordinate user interaction.
- Location: `frontend/src/app`, `frontend/src/components`
- Contains: App Router pages, domain components, local UI state, loading/error boundaries.
- Depends on: `frontend/src/hooks`, `frontend/src/stores`, `frontend/src/lib`, `frontend/src/components/ui`.
- Used by: Browser users through `frontend/src/app/layout.tsx`.

**Frontend Client Boundary:**
- Purpose: Isolate REST/SSE transport and expose client-side DTOs.
- Location: `frontend/src/lib`
- Contains: The central `frontend/src/lib/api.ts`, domain-specific clients such as `frontend/src/lib/derivative-api.ts`, and `frontend/src/lib/sse.ts`.
- Depends on: Axios, Fetch, browser session storage, backend route strings.
- Used by: Pages, components, hooks, and Zustand stores throughout `frontend/src`.
- Constraint: DTOs are hand-maintained mirrors of `backend/app/schemas`; no generated or shared TypeScript contract package exists.

**FastAPI Route Layer:**
- Purpose: Authenticate actors, enforce owner-scoped resource access, validate Pydantic inputs, and translate service outcomes into HTTP.
- Location: `backend/app/api`
- Contains: More than 40 registered route groups, composed in `backend/app/main.py`.
- Depends on: `backend/app/core`, `backend/app/models`, `backend/app/schemas`, `backend/app/services`.
- Used by: Frontend REST clients, Agent Service callbacks/tools, CI and integration tests.

**Backend Domain/Service Layer:**
- Purpose: Execute business workflows and enforce domain state transitions.
- Location: `backend/app/services`
- Contains: Flat legacy coordinators such as `backend/app/services/import_service.py` plus domain packages such as `backend/app/services/queryplan`, `backend/app/services/narrative_memory`, and `backend/app/services/derivative_generation`.
- Depends on: `backend/app/models`, `backend/app/schemas`, `backend/app/core`, external provider clients.
- Used by: `backend/app/api`, background tasks, backend scripts, and other service packages.

**Contract Layer:**
- Purpose: Validate API payloads and frozen domain envelopes.
- Location: `backend/app/schemas`, `agent-service/src/skills/*/*.schema.json`, `agent-service/src/tools/registry.ts`
- Contains: Pydantic models, JSON Schema skill contracts, TypeBox tool parameters.
- Depends on: Mostly Pydantic/domain types, but `backend/app/schemas/canon_space.py` and `backend/app/schemas/creative_generation.py` import service-layer contracts from `backend/app/services/canon_fork/contracts.py` and `backend/app/services/canon_space_policy.py`.
- Used by: FastAPI routes, backend services, Agent Service loader/governance, frontend manual mirrors.

**Persistence and Infrastructure Layer:**
- Purpose: Durable SQL state, vector indexes, security, configuration, logging, files, and migrations.
- Location: `backend/app/models`, `backend/app/core`, `backend/migrations`, `backend/app/services/vector_store.py`, `backend/storage`
- Contains: SQLAlchemy models, async session factory, JWT/crypto helpers, Chroma client, Alembic revisions.
- Depends on: `backend/app/config.py` and external PostgreSQL/Chroma services.
- Used by: Backend services and route dependencies.

**Agent Execution Layer:**
- Purpose: Load governed skills, create Pi sessions, expose tools, stream events, request approvals, and submit final envelopes.
- Location: `agent-service/src`
- Contains: `agent`, `skills`, `tools`, `policy`, `governance`, `structured-output`, `transport`, `mcp`.
- Depends on: Pi packages, AJV/TypeBox/YAML, and FastAPI HTTP endpoints.
- Used by: Browser `/agent/*` streams and the background poller in `agent-service/src/poller.ts`.

## Data Flow

### Primary REST Request Path

1. A page or component calls a domain client through the shared Axios instance (`frontend/src/lib/api.ts:25`).
2. Next.js rewrites `/api/:path*` to FastAPI (`frontend/next.config.mjs:12`).
3. `backend/app/main.py:239` onward dispatches to a router in `backend/app/api`; dependencies in `backend/app/api/dependencies.py`/`backend/app/core/security.py` resolve DB and actor ownership.
4. The router calls a service in `backend/app/services`, which reads/writes models in `backend/app/models` through `backend/app/core/database.py`.
5. Pydantic schemas in `backend/app/schemas` serialize the response; the manually mirrored TypeScript DTO in `frontend/src/lib` is consumed by UI state.

### Novel Import and Index Flow

1. Upload enters `backend/app/api/novels.py:79`, creates a durable import job through `backend/app/services/import_service.py:48`, then schedules background processing.
2. `backend/app/services/import_service.py` parses and persists `Novel`, `Chapter`, and import state using `backend/app/models/novel.py` and `backend/app/models/import_job.py`.
3. Import invokes `backend/app/services/indexing_service.py:99`, which coordinates `backend/app/services/chunking_service.py`, embedding through `backend/app/services/ai_service.py`/`backend/app/services/local_embed.py`, and index journaling.
4. Relational chunks are stored through `backend/app/models/text_chunk.py`; vector records are written through `backend/app/services/vector_store.py` to ChromaDB.
5. Index failures leave explicit journal/import status and a rebuild path rather than rolling back the already persisted novel.

### Retrieval / QueryPlan Flow

1. Search/RAG/reader/agent entry points in `backend/app/api/search.py`, `backend/app/api/rag.py`, `backend/app/api/reader_chat.py`, or `backend/app/api/agent_tools.py` validate owner and cutoff.
2. Retrieval is coordinated by `backend/app/services/hybrid_search.py`, `backend/app/services/queryplan/service.py`, or domain adapters in `backend/app/services/queryplan/adapters.py`.
3. Keyword evidence comes from PostgreSQL text chunks; semantic evidence comes from `backend/app/services/vector_store.py`; world/narrative dimensions come from `backend/app/services/world_model` and `backend/app/services/narrative_memory`.
4. `backend/app/services/queryplan/fusion.py` fuses results, and `backend/app/services/queryplan/evidence.py` materializes leaf evidence with source lineage.
5. Consumers receive evidence-bearing DTOs and must not replace leaf citations with summaries.

### Interactive Agent Run

1. UI calls `streamAgentRun()` (`frontend/src/lib/sse.ts:71`) against `/agent/novels/{novel_id}/runs`; Next rewrites it to Agent Service (`frontend/next.config.mjs:21`).
2. `agent-service/src/server.ts:399` asks FastAPI to route intent, resolves an active skill version, and posts the run to `backend/app/api/agent.py:222`.
3. FastAPI commits the run before dispatch and returns a one-time internal token. Durable run truth remains in `backend/app/models/agent_runtime.py`.
4. Agent Service loads `agent-service/src/skills/<skill>`, creates a Pi session via `agent-service/src/agent/session-factory.ts`, and exposes tools from `agent-service/src/tools/registry.ts`.
5. Every domain tool calls `/api/agent-tools/{tool}` through `agent-service/src/tools/fastapi-client.ts`; `backend/app/services/agent_tools/facade.py` re-enforces owner/novel/cutoff/budget rules.
6. Agent events stream to the browser through `agent-service/src/transport/sse.ts`; approval requests round-trip through `backend/app/api/agent.py` and `frontend/src/components/analysis/approval-request-dialog.tsx`.
7. On successful stop, Agent Service posts the envelope to `backend/app/api/agent.py:366`; `backend/app/services/agent_runtime/finalize.py` and `structured_output_integrity.py` are authoritative for artifact creation. Cancel/error paths write no artifact.

### Queued Agent Backfill

1. Backend creates `origin='chat_backfill'` runs through `backend/app/services/agent_runtime/backfill.py`.
2. `agent-service/src/poller.ts:282` pulls `/api/agent/queued-runs`; FastAPI exposes only queued backfills at `backend/app/api/agent.py:707`.
3. Poller atomically claims with a lease at `backend/app/api/agent.py:748`, executes the same skill/session pipeline, and finalizes through FastAPI.
4. Successful backfill artifacts are materialized into candidate domain rows by `backend/app/services/agent_runtime/materialize.py` and `materializers.py`; publication/promotion remains a separate explicit action.

**State Management:**
- Browser-local UI state uses component state, hooks under `frontend/src/hooks`, and Zustand stores under `frontend/src/stores`.
- Durable product and Agent state lives in PostgreSQL models under `backend/app/models`.
- Agent Service keeps live Pi sessions in memory in `agent-service/src/server.ts`; it does not own durable run state.
- Background jobs use database status machines, leases, journals, manifests, and retries in domain-specific service/model modules.

## Key Abstractions

**Owner-Scoped Resource Boundary:**
- Purpose: Bind every novel/domain operation to an authenticated actor.
- Examples: `backend/app/api/dependencies.py`, `backend/app/core/security.py`, `backend/app/services/agent_tools/facade.py`
- Pattern: FastAPI dependency plus owner predicates in repository/service queries; Agent tools additionally bind the run novel ID server-side.

**Version + Active Pointer + Candidate:**
- Purpose: Keep generated analysis immutable/auditable and separate candidate state from accepted product truth.
- Examples: `backend/app/models/narrative_memory.py`, `backend/app/models/visual_bible.py`, `backend/app/models/key_scene.py`, `backend/app/models/analysis.py`
- Pattern: Immutable/versioned records, evidence refs, explicit review/promotion, and an active pointer or published view.

**Frozen Manifest / EvidenceRef:**
- Purpose: Make outputs reproducible and prevent citations outside the authorized source snapshot.
- Examples: `backend/app/services/queryplan/evidence.py`, `backend/app/services/agent_runtime/finalize.py`, `backend/app/services/agent_runtime/structured_output_integrity.py`
- Pattern: Hash and retain source lineage, validate output refs against an allowlist, fail closed on drift.

**Skill Contract:**
- Purpose: Package agent instructions, input/output shape, tools, and policy as a versioned unit.
- Examples: `agent-service/src/skills/answer-reading-question/skill.yaml`, `agent-service/src/skills/answer-reading-question/input.schema.json`, `agent-service/src/skills/loader.ts`
- Pattern: YAML manifest + JSON schemas + Markdown instructions, validated during loading/governance and registered durably by FastAPI.

**Domain Tool Facade:**
- Purpose: Keep model/tool execution separated from database and authority logic.
- Examples: `agent-service/src/tools/registry.ts`, `agent-service/src/tools/fastapi-client.ts`, `backend/app/api/agent_tools.py`, `backend/app/services/agent_tools/facade.py`
- Pattern: TypeBox tool → bounded HTTP call → backend authorization/domain query → frozen error envelope.

## Entry Points

**FastAPI ASGI:**
- Location: `backend/app/main.py`
- Triggers: Uvicorn/Gunicorn or backend container.
- Responsibilities: Startup recovery, AI routing preference restoration, middleware, exceptions, router mount, `/api/health`.

**Next.js Web:**
- Location: `frontend/src/app/layout.tsx`, `frontend/src/app/page.tsx`
- Triggers: Next.js server/browser navigation.
- Responsibilities: Root layout, auth gate, shell, page routing, `/api` and `/agent` proxying through `frontend/next.config.mjs`.

**Agent Service:**
- Location: `agent-service/start.mjs`, `agent-service/src/server.ts`
- Triggers: Node process or service container.
- Responsibilities: Startup governance, `/healthz`, `/agent/novels/{id}/runs`, optional queued-run poller.

**Alembic:**
- Location: `backend/migrations/env.py`, `backend/alembic.ini`
- Triggers: `alembic upgrade head` and schema checks.
- Responsibilities: Load metadata from `backend/app/models/__init__.py` and apply the migration chain in `backend/migrations/versions`.

**Operational/CI Entrypoints:**
- Location: `Makefile`, `.github/workflows`, `scripts`, `tests/ci`
- Triggers: Local make targets and GitHub Actions.
- Responsibilities: Test gates, quality/nightly orchestration, deployment/build helpers.

## Architectural Constraints

- **Threading:** FastAPI uses an async event loop and `AsyncSession`; blocking Chroma calls are wrapped with `asyncio.to_thread` in `backend/app/services/vector_store.py`. Agent Service uses the Node event loop and bounded poller concurrency in `agent-service/src/poller.ts`.
- **Global state:** `backend/app/config.py` exposes a settings singleton; `backend/app/services/ai_router.py`, `backend/app/services/vector_store.py`, and `backend/app/services/import_service.py` expose module-level service instances. Agent startup manifest/config are process state in `agent-service/src/server.ts` and `agent-service/src/config.ts`.
- **Durability boundary:** FastAPI/PostgreSQL is authoritative. Do not add direct database writes or durable session state to `agent-service/src`.
- **Transport boundary:** Frontend REST must use `/api`; interactive agent runs must use `/agent`. Bypassing `frontend/next.config.mjs` changes auth/CORS and streaming behavior.
- **Shared contracts:** There is no shared generated SDK. Contract changes require synchronized edits across `backend/app/schemas`, `backend/app/api`, `frontend/src/lib`, `agent-service/src/tools/registry.ts`, `agent-service/src/tools/fastapi-client.ts`, and skill JSON schemas as applicable.
- **Circular imports:** Static analysis of current source finds cycles in `backend/app/services/narrative_memory/{builder_contracts,builder_gateway,builder_packages,builder_repository}.py`, `backend/app/services/derivative_editor/{chapters,revisions}.py`, `backend/app/services/chunking/baseline.py` ↔ `backend/app/services/chunking_service.py`, `backend/app/services/prompt_compiler/{adapters,serialization}.py`, and `backend/app/services/world_model/{entities,provenance}.py`. Some cycles are hidden by function-local imports.
- **Frontend circular imports:** Current local import graph includes a five-file analysis/reader cycle across `frontend/src/components/analysis/{analysis-chat-panel,agent-turn-inline,cited-answer-artifact,world-model-evidence-panel}.tsx` and `frontend/src/components/reader/reader-chat-panel.tsx`, plus `frontend/src/components/writing/markdown-editor.tsx` ↔ `revision-history.tsx` (the reverse edge is type-only).
- **Cross-layer imports:** Schemas import service constants/contracts in `backend/app/schemas/creative_generation.py` and `backend/app/schemas/canon_space.py`; new schema modules should not extend this inversion.

## Coupling and Change Amplification

| Hub / Boundary | Evidence | Change amplification |
|---|---|---|
| Backend router hub | `backend/app/main.py` imports/registers every router | Adding/removing a route always touches the composition root; import-time failures can prevent the whole API from starting. |
| ORM barrel | `backend/app/models/__init__.py` imports nearly every model for metadata | A model import cycle or optional dependency affects Alembic and application startup; every new table requires barrel and migration coordination. |
| Frontend API hub | `frontend/src/lib/api.ts` is ~1,340 lines and imported by 70+ source modules | DTO or auth-client changes have broad compile/test blast radius; prefer new bounded `*-api.ts` modules using the shared `api` instance. |
| Agent HTTP runtime | `agent-service/src/server.ts` is ~788 lines and owns routing, approval, SSE, run lifecycle, and finalization | Any run-protocol change affects browser frames, FastAPI run endpoints, agent tests, and structured output. Extract only along existing `transport`, `policy`, `agent`, and `structured-output` boundaries. |
| Tool contract bridge | `agent-service/src/tools/registry.ts`, `backend/app/api/agent_tools.py`, `backend/app/services/agent_tools/facade.py` | Tool additions require aligned name, TypeBox input, backend request schema/handler, allowlists, permissions, and contract tests. |
| Structured output authority | `backend/app/services/agent_runtime/structured_output_integrity.py` imports many domain contracts | Adding an artifact/envelope type changes Agent builder, skill schema, backend integrity dispatch, materializer, frontend preview, and tests. |
| Narrative memory worker cluster | `backend/app/services/narrative_memory/builder_worker.py` and the four-file SCC | Contract/repository changes propagate through builder, recovery, qualification, audit, and agent output validation. |
| Frontend page/component hubs | `frontend/src/app/analysis/page.tsx`, `frontend/src/app/novels/[id]/page.tsx` | Domain additions can enlarge page-level orchestration and tests; place reusable behavior in domain components and clients first. |

## Anti-Patterns

### Hand-Copying a Contract Without a Cross-Boundary Test

**What happens:** Backend Pydantic DTOs are manually reproduced in `frontend/src/lib/api.ts` and other `*-api.ts` files; agent tool names/errors/parameters are mirrored between `backend/app/services/agent_tools` and `agent-service/src/tools`.
**Why it's wrong:** A single-side edit compiles locally but fails at runtime or silently drops fields.
**Do this instead:** Change all producers/consumers in one slice and add/adjust contract tests in `backend/tests/contract`, `frontend/src/lib/*.contract.test.ts`, and `agent-service/tests`.

### Importing Service Implementations Into Schemas

**What happens:** `backend/app/schemas/canon_space.py` imports `backend/app/services/canon_fork/contracts.py`, and `backend/app/schemas/creative_generation.py` imports `backend/app/services/canon_space_policy.py`.
**Why it's wrong:** The contract layer becomes dependent on implementation modules, increasing import cycles and making schemas unusable independently.
**Do this instead:** Place shared constants/value objects in `backend/app/schemas` or a neutral contract module, then let services depend on that module.

### Adding More Responsibilities to Central Hubs

**What happens:** `frontend/src/lib/api.ts`, `agent-service/src/server.ts`, `backend/app/services/agent_tools/facade.py`, and `backend/app/services/agent_runtime/structured_output_integrity.py` already coordinate many domains.
**Why it's wrong:** Small feature changes trigger broad reviews, merge conflicts, and regression matrices.
**Do this instead:** Add a domain client under `frontend/src/lib/<domain>-api.ts`, a backend package under `backend/app/services/<domain>`, or an Agent module under the existing `agent-service/src` sublayer; keep the hub to dispatch/registration.

### Bypassing FastAPI Authority From Agent Service

**What happens:** A tempting shortcut is to persist Agent state or call storage directly from `agent-service/src`.
**Why it's wrong:** It bypasses owner scoping, cutoff policy, frozen evidence, idempotent finalization, and audit lineage.
**Do this instead:** Expose a bounded backend endpoint under `backend/app/api/agent.py` or `backend/app/api/agent_tools.py`, implement authority in `backend/app/services`, and call it through `agent-service/src/tools/fastapi-client.ts`.

### Creating New Function-Local Imports to Mask Cycles

**What happens:** Several backend coordinators, including `backend/app/api/agent.py` and `backend/app/services/agent_runtime/materializers.py`, defer imports inside functions.
**Why it's wrong:** Import cycles become runtime-path dependent and harder for static tools to reveal.
**Do this instead:** Move shared contracts/helpers to a lower neutral module and enforce a one-way dependency between domain services.

## Error Handling

**Strategy:** Validate at each boundary, preserve explicit domain states, and fail closed when authority or evidence cannot be proven.

**Patterns:**
- FastAPI has global exception and validation handlers in `backend/app/main.py`; agent-tool paths use the frozen `{error: {code, message}}` envelope from `backend/app/services/agent_tools/errors.py`.
- Route modules convert domain state conflicts to HTTP status codes; state-machine services retain durable `status_reason`/`error_code` in models such as `backend/app/models/agent_runtime.py` and `backend/app/models/import_job.py`.
- Agent Service normalizes backend errors in `agent-service/src/tools/fastapi-client.ts`, bounds tool calls by timeout/size, and avoids exposing tokens.
- Agent finalization in `backend/app/services/agent_runtime/finalize.py` is idempotent and is the only artifact write boundary; cancelled/failed runs produce zero artifacts.
- Frontend Axios callers handle rejected requests at hooks/components; `frontend/src/lib/sse.ts` reports malformed frames without inventing replacement content.

## Cross-Cutting Concerns

**Logging:** Structured request logging is configured through `backend/app/core/logging.py` and mounted in `backend/app/main.py`; Agent Service deliberately excludes authorization tokens in `agent-service/src/tools/fastapi-client.ts`.
**Validation:** Pydantic validates backend HTTP contracts, TypeBox validates agent tool parameters, AJV validates skill input/output schemas, and frozen evidence is revalidated server-side in `backend/app/services/agent_runtime/structured_output_integrity.py`.
**Authentication:** Browser uses Bearer JWT/session token through `frontend/src/lib/api.ts`; backend resolves users through `backend/app/core/security.py`; service polling uses a gateway token; each Agent run receives a hashed, scoped internal token from `backend/app/api/agent.py`.
**Authorization:** Owner/novel scoping is enforced in backend dependencies and queries, never trusted from model/tool parameters. Branch/cutoff/canon authority is enforced in domain policies under `backend/app/services`.
**Transactions:** Services use async SQLAlchemy sessions; multi-step workflows persist explicit state/journals. Agent run acceptance commits before dispatch, and finalization owns artifact transactionality.

---

*Architecture analysis: 2026-08-07*
