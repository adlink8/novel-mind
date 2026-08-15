---
last_mapped_commit: b679b49
---
# Codebase Structure

**Analysis Date:** 2026-08-07

## Directory Layout

```text
novel-mind-new/
├── backend/                     # FastAPI application, migrations, evals, tests, storage
│   ├── app/
│   │   ├── main.py              # ASGI composition root
│   │   ├── config.py            # NOVELMIND_-prefixed application settings
│   │   ├── api/                 # HTTP routers and dependencies
│   │   ├── core/                # DB, auth, crypto, URL security, logging
│   │   ├── models/              # SQLAlchemy ORM/domain persistence
│   │   ├── schemas/             # Pydantic API and frozen contracts
│   │   └── services/            # Domain workflows and external adapters
│   ├── migrations/versions/     # Alembic revision chain
│   ├── tests/                   # pytest unit/integration/security/contract suites
│   ├── evals/                   # Evaluation fixtures, pricing, calibration, results
│   ├── prompts/                 # Prompt assets
│   ├── scripts/                 # Backend maintenance/backfill commands
│   ├── storage/                 # Generated illustration/derivative assets
│   └── uploads/                 # Runtime novel uploads
├── frontend/                    # Next.js browser application
│   ├── src/
│   │   ├── app/                 # App Router pages/layout/loading/error routes
│   │   ├── components/          # Domain and shared UI components
│   │   ├── hooks/               # Component-facing data hooks
│   │   ├── lib/                 # REST/SSE clients, DTO mirrors, utilities
│   │   ├── stores/              # Zustand global stores
│   │   └── __tests__/           # Shared Vitest setup and coverage policy
│   ├── e2e/                     # Playwright workflows
│   ├── public/                  # Static assets/PWA icons
│   └── next.config.mjs          # `/api` and `/agent` rewrites
├── agent-service/               # Node/Pi Agent sidecar
│   ├── src/
│   │   ├── agent/               # Pi provider/session/resource construction
│   │   ├── governance/          # Lockfile, permissions, tool manifest gates
│   │   ├── mcp/                 # MCP isolation/adapter boundary
│   │   ├── policy/              # Tool visibility and approval policy
│   │   ├── skills/              # Versioned Skill packages and schemas
│   │   ├── structured-output/   # Agent envelope builders/validation
│   │   ├── tools/               # Domain tool registry and FastAPI adapter
│   │   ├── transport/           # SSE framing
│   │   ├── server.ts            # Interactive HTTP/SSE runtime
│   │   └── poller.ts            # Queued backfill pull worker
│   ├── tests/                   # Vitest runtime/governance/skill tests
│   ├── qualification/           # Agent qualification assets
│   ├── scripts/                 # Package/lock governance scripts
│   ├── spikes/                  # Pi SDK feasibility evidence
│   └── vendor/pi-packages/      # Vendored package metadata/source snapshot
├── tests/ci/                    # Repository-level CI policy tests
├── scripts/                     # Cross-project verification and operations scripts
├── deploy/                      # Deployment configuration
├── docs/                        # Human-facing architecture/product/operations docs
├── .github/                     # CI workflows and repository automation
├── .quality/                    # Quality baselines/thresholds
├── .planning/                   # GSD state, roadmap, plans, codebase maps
├── Makefile                     # Common developer/CI commands
├── docker-compose.yml           # Local multi-service topology
└── docker-compose.ci.yml        # CI service topology
```

Tracked source concentration at commit `b679b49`: `backend/app` contains 416 files, `backend/tests` 406, `frontend/src` 188, `frontend/e2e` 29, `agent-service/src` 113, and `agent-service/tests` 28. Use these subtrees as the primary implementation and verification boundaries.

## Directory Purposes

**`backend/app/api`:**
- Purpose: Define HTTP endpoints, inject actor/DB dependencies, validate request context, and map service errors to responses.
- Contains: One module per HTTP/domain surface plus `backend/app/api/dependencies.py`.
- Key files: `backend/app/api/agent.py`, `backend/app/api/agent_tools.py`, `backend/app/api/novels.py`, `backend/app/api/reader_chat.py`, `backend/app/api/derivative_export.py`.
- Addition rule: Add route logic here only after the domain operation exists under `backend/app/services`; register a new router in `backend/app/main.py`.

**`backend/app/services`:**
- Purpose: Own business rules, workflow state machines, retrieval, generation, review, and publication.
- Contains: Legacy flat services and domain packages.
- Key files: `backend/app/services/import_service.py`, `backend/app/services/queryplan/service.py`, `backend/app/services/agent_tools/facade.py`, `backend/app/services/agent_runtime/finalize.py`.
- Addition rule: Prefer `backend/app/services/<domain>/` with focused modules when a feature has multiple operations; do not grow the large central hubs unless adding dispatch only.

**`backend/app/services/agent_runtime`:**
- Purpose: Durable skill registry/run/artifact/approval lifecycle and deterministic domain materialization.
- Contains: Registration, approvals, finalization, output integrity, artifact revisions, routing, materializers, backfill.
- Key files: `backend/app/services/agent_runtime/registry.py`, `backend/app/services/agent_runtime/finalize.py`, `backend/app/services/agent_runtime/structured_output_integrity.py`, `backend/app/services/agent_runtime/materializers.py`.

**`backend/app/services/queryplan`:**
- Purpose: Question-driven retrieval planning, dimension adapters, fusion, evidence, and durable traces.
- Contains: `contracts.py`, `parser.py`, `adapters.py`, `fusion.py`, `evidence.py`, `repository.py`, `service.py`.
- Key files: `backend/app/services/queryplan/contracts.py`, `backend/app/services/queryplan/service.py`.

**`backend/app/services/narrative_memory`:**
- Purpose: Whole-book hierarchical memory build, retrieval, rebuild, qualification, audit, and provenance.
- Contains: Builder worker/repository/contracts/packages, manifests, dependency graph, carry-forward, qualification, audit.
- Key files: `backend/app/services/narrative_memory/builder_worker.py`, `backend/app/services/narrative_memory/contracts.py`, `backend/app/services/narrative_memory/routing.py`.
- Constraint: This is the densest service cluster and contains a circular import SCC; extract shared contracts before introducing new cross-module dependencies.

**`backend/app/services/derivative_*` and `backend/app/services/canon_fork`:**
- Purpose: Triple-space authority, derivative editor/revisions, constrained generation, visual assets, and reproducible export.
- Contains: Separate packages `backend/app/services/canon_fork`, `derivative_editor`, `derivative_generation`, `derivative_visual`, and `derivative_export`.
- Key files: `backend/app/services/canon_fork/snapshot.py`, `backend/app/services/derivative_editor/revisions.py`, `backend/app/services/derivative_generation/runner.py`, `backend/app/services/derivative_export/package.py`.
- Addition rule: Preserve branch namespace and original-canon immutability across all packages.

**`backend/app/models`:**
- Purpose: Define SQLAlchemy persistence models and relationships.
- Contains: Domain model files and the metadata barrel `backend/app/models/__init__.py`.
- Key files: `backend/app/models/base.py`, `backend/app/models/novel.py`, `backend/app/models/agent_runtime.py`, `backend/app/models/canon_space.py`.
- Addition rule: Put a new domain table in a focused model file, export it from `backend/app/models/__init__.py`, and add an Alembic revision under `backend/migrations/versions`.

**`backend/app/schemas`:**
- Purpose: Define Pydantic v2 HTTP DTOs and reusable frozen contracts.
- Contains: Domain files plus the export barrel `backend/app/schemas/__init__.py`.
- Key files: `backend/app/schemas/agent_runtime.py`, `backend/app/schemas/scene_spec.py`, `backend/app/schemas/canon_space.py`.
- Addition rule: Keep schemas independent of services; avoid repeating the existing inversions in `backend/app/schemas/canon_space.py` and `creative_generation.py`.

**`backend/app/core`:**
- Purpose: Cross-cutting infrastructure only.
- Contains: `database.py`, `security.py`, `crypto.py`, `url_security.py`, `logging.py`.
- Key files: `backend/app/core/database.py`, `backend/app/core/security.py`.
- Addition rule: Place code here only when it is domain-agnostic and used broadly by routes/services.

**`backend/tests`:**
- Purpose: Verify backend behavior and policy at unit, integration, adversarial, security, contract, CI, and live levels.
- Contains: `backend/tests/unit/<domain>`, `backend/tests/integration/<domain>`, `backend/tests/adversarial`, `backend/tests/security`, `backend/tests/contract`, `backend/tests/ci`, `backend/tests/live`.
- Key files: `backend/tests/conftest.py`, `backend/tests/README.md`.
- Addition rule: Co-locate the test by domain and test type; cross-runtime contracts belong in `backend/tests/contract` or `backend/tests/integration/agent_runtime`.

**`frontend/src/app`:**
- Purpose: Map URL routes to page compositions and route-level loading/error states.
- Contains: Pages for novels/reader, analysis, evaluation, search, settings, writing, and prototype routes.
- Key files: `frontend/src/app/layout.tsx`, `frontend/src/app/analysis/page.tsx`, `frontend/src/app/novels/[id]/page.tsx`, `frontend/src/app/writing/page.tsx`.
- Addition rule: Put page-specific orchestration here; move reusable visual/workflow logic to `frontend/src/components/<domain>`.

**`frontend/src/components`:**
- Purpose: Reusable and domain UI components.
- Contains: `analysis`, `reader`, `relationships`, `clues`, `structure`, `visual-bible`, `key-scenes`, `scene-spec`, `illustrations`, `writing`, and `ui`.
- Key files: `frontend/src/components/app-shell.tsx`, `frontend/src/components/analysis/analysis-unified-chat.tsx`, `frontend/src/components/reader/reader-content.tsx`.
- Addition rule: Use `frontend/src/components/ui` for generic primitives and a domain directory for product-specific components.

**`frontend/src/lib`:**
- Purpose: Browser transport, client DTOs, and non-visual utilities.
- Contains: Central REST API, domain clients, SSE parser, routing helpers, selection and UI utilities.
- Key files: `frontend/src/lib/api.ts`, `frontend/src/lib/sse.ts`, `frontend/src/lib/derivative-api.ts`, `frontend/src/lib/agent-routing.ts`.
- Addition rule: New large domains receive `frontend/src/lib/<domain>-api.ts` using the exported `api` instance; do not duplicate Axios setup or `/api` prefixes.

**`frontend/src/hooks` and `frontend/src/stores`:**
- Purpose: Hooks own component-facing asynchronous behavior; Zustand stores own genuinely shared browser state.
- Contains: `frontend/src/hooks/use-novels.ts`, `frontend/src/hooks/use-ai-models.ts`, `frontend/src/stores/novelStore.ts`, `frontend/src/stores/aiConfigStore.ts`.
- Addition rule: Keep server authority in backend; stores cache/UI-coordinate rather than duplicate domain rules.

**`agent-service/src/skills`:**
- Purpose: Store versioned agent capabilities as self-contained packages.
- Contains: One folder per skill with `skill.yaml`, `SKILL.md`, `input.schema.json`, `output.schema.json`, examples, and tests.
- Key files: `agent-service/src/skills/loader.ts`, `agent-service/src/skills/answer-reading-question/skill.yaml`.
- Addition rule: Add all package artifacts together and update governance/registry tests; do not create unversioned prompt-only skills.

**`agent-service/src/tools`:**
- Purpose: Define Pi-visible tools and forward them to the authoritative backend facade.
- Contains: `agent-service/src/tools/registry.ts`, `agent-service/src/tools/fastapi-client.ts`.
- Addition rule: Add corresponding backend schema/endpoint/facade behavior under `backend/app/api/agent_tools.py` and `backend/app/services/agent_tools` in the same change.

**`agent-service/src/governance`, `policy`, `structured-output`, `transport`:**
- Purpose: Keep runtime governance, authorization policy, output construction, and SSE mechanics separate from `agent-service/src/server.ts`.
- Contains: Lock/manifest gates, approval sessions, envelope builders, stream framing.
- Key files: `agent-service/src/governance/tool-registry-manifest.ts`, `agent-service/src/policy/engine.ts`, `agent-service/src/structured-output/validator.ts`, `agent-service/src/transport/sse.ts`.

**`.planning`:**
- Purpose: Authoritative GSD planning/execution workspace.
- Contains: `STATE.md`, `ROADMAP.md`, `config.json`, phase plans/evidence, and these codebase maps.
- Key files: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/config.json`, `.planning/codebase/ARCHITECTURE.md`.
- Addition rule: Product source does not live here; update planning artifacts only through the applicable GSD workflow.

## Key File Locations

**Entry Points:**
- `backend/app/main.py`: FastAPI ASGI composition root.
- `frontend/src/app/layout.tsx`: Browser root layout and application shell boundary.
- `frontend/src/app/page.tsx`: Home route.
- `agent-service/start.mjs`: Node startup wrapper.
- `agent-service/src/server.ts`: Agent HTTP/SSE entry point.
- `agent-service/src/poller.ts`: Queued-run worker entry point.
- `backend/migrations/env.py`: Alembic migration entry point.

**Configuration:**
- `backend/app/config.py`: Backend settings model; environment configuration exists in ignored `.env` files and must not be committed/read into documentation.
- `backend/pytest.ini`: Backend pytest defaults and markers.
- `backend/ruff.toml`: Backend lint configuration.
- `frontend/next.config.mjs`: Backend/Agent rewrites and Next configuration.
- `frontend/tsconfig.json`: Frontend compiler and `@/*` alias configuration.
- `frontend/vitest.config.ts`: Frontend unit test configuration.
- `frontend/playwright.config.ts`: Browser test projects.
- `agent-service/src/config.ts`: Agent runtime configuration.
- `agent-service/packages.lock.json`: Governed Pi/MCP package manifest.
- `agent-service/tsconfig.json`: Agent compiler configuration.
- `agent-service/vitest.config.ts`: Agent test configuration.
- `.github/workflows`: CI/nightly pipelines.
- `Makefile`: Repository command surface.

**Core Logic:**
- `backend/app/services/import_service.py`: Import state machine.
- `backend/app/services/indexing_service.py`: Chunk/embed/vector indexing coordinator.
- `backend/app/services/queryplan/service.py`: Question-driven retrieval coordinator.
- `backend/app/services/agent_runtime/finalize.py`: Authoritative Agent finalization.
- `backend/app/services/agent_runtime/structured_output_integrity.py`: Artifact integrity dispatcher.
- `backend/app/services/agent_tools/facade.py`: Agent domain tool authority.
- `agent-service/src/server.ts`: Interactive Agent lifecycle.
- `frontend/src/lib/api.ts`: Shared REST transport and core DTOs.

**Testing:**
- `backend/tests/unit`: Backend domain unit tests.
- `backend/tests/integration`: Backend persistence/workflow integration tests.
- `backend/tests/contract`: OpenAPI and frozen cross-boundary contracts.
- `agent-service/tests`: Agent runtime, governance, tool, and envelope tests.
- `frontend/src/**/*.test.tsx`: Co-located UI/client tests.
- `frontend/e2e`: Playwright end-to-end workflows.
- `tests/ci`: Repository-level CI policy tests.

**Human Documentation:**
- `docs/architecture`: System/module/data/request-flow architecture guides.
- `README.md`: Project setup and overview.
- `IMPLEMENTATION-STATUS.md`: Human-facing implementation status.
- `AGENTS.md`: Repository-specific AI/GSD rules.

## Naming Conventions

**Files:**
- Python modules use `snake_case.py`: `backend/app/services/creative_consistency.py`.
- Python multi-file domains use a snake-case package and focused noun/verb modules: `backend/app/services/derivative_export/materializer.py`.
- React components use lowercase kebab-case filenames: `frontend/src/components/analysis/agent-workspace-panel.tsx`.
- Frontend hooks use `use-<noun>.ts`: `frontend/src/hooks/use-novels.ts`.
- Frontend API modules use `<domain>-api.ts`: `frontend/src/lib/visual-bible-api.ts`.
- Tests use `.test.ts`, `.test.tsx`, or Python `test_*.py`, near their framework's established test root.
- Agent skill folders use kebab-case: `agent-service/src/skills/continue-derivative-story`.
- Alembic revisions use revision identifiers plus snake-case descriptions under `backend/migrations/versions`.

**Directories:**
- Python domain packages use snake_case: `backend/app/services/narrative_memory`.
- Frontend/Agent TypeScript domain directories use kebab-case where multiword: `frontend/src/components/visual-bible`, `agent-service/src/structured-output`.
- Tests mirror source domains: `backend/tests/unit/derivative_generation`, `backend/tests/integration/queryplan`.

## Where to Add New Code

**New Backend Feature:**
- HTTP route: `backend/app/api/<domain>.py`
- Business logic: `backend/app/services/<domain>/`
- Persistence: `backend/app/models/<domain>.py`
- API contracts: `backend/app/schemas/<domain>.py`
- Migration: `backend/migrations/versions/<revision>_<description>.py`
- Tests: `backend/tests/unit/<domain>` and `backend/tests/integration/<domain>`
- Registration: `backend/app/main.py`; model metadata export in `backend/app/models/__init__.py` when a table is added.

**New Frontend Feature:**
- Page composition: `frontend/src/app/<route>/page.tsx`
- Domain UI: `frontend/src/components/<domain>/`
- REST client/DTOs: `frontend/src/lib/<domain>-api.ts`
- Shared state only if needed across components: `frontend/src/stores/<domain>Store.ts`
- Component/client tests: co-located `*.test.tsx` or `*.test.ts`
- Browser workflow: `frontend/e2e/<workflow>.spec.ts`

**New Agent Skill:**
- Package: `agent-service/src/skills/<skill-name>/skill.yaml`, `SKILL.md`, input/output schemas, examples, and tests.
- Envelope builder/normalization when a new output family is required: `agent-service/src/structured-output`.
- Backend registry/finalization support: `backend/app/services/agent_runtime/registry.py`, `structured_output_integrity.py`, and optional `materializers.py`.
- Frontend artifact view: `frontend/src/components/analysis` or the owning domain component.
- Tests: `agent-service/tests/skills`, `backend/tests/integration/agent_runtime`, and frontend contract/component tests.

**New Agent Tool:**
- Backend input/handler: `backend/app/api/agent_tools.py`
- Backend authority/query: `backend/app/services/agent_tools/facade.py`
- Backend error code if necessary: `backend/app/services/agent_tools/errors.py`
- Agent TypeBox definition/allowlist: `agent-service/src/tools/registry.ts`
- HTTP/error mirror: `agent-service/src/tools/fastapi-client.ts`
- Governance permissions/manifests: `agent-service/src/governance` and `agent-service/packages.lock.json`
- Contract tests: `backend/tests/contract` and `agent-service/tests`.

**New Reusable UI Component:**
- Generic primitive: `frontend/src/components/ui/<name>.tsx`
- Product/domain component: `frontend/src/components/<domain>/<name>.tsx`
- Do not place reusable components in `frontend/src/app/<route>`.

**Utilities:**
- Backend domain-neutral infrastructure: `backend/app/core/<name>.py`
- Backend domain helper: `backend/app/services/<domain>/<name>.py`
- Frontend pure/browser helper: `frontend/src/lib/<name>.ts`
- Agent runtime helper: the owning sublayer under `agent-service/src`, not `agent-service/src/server.ts`.

## Special Directories

**`backend/migrations/versions`:**
- Purpose: Alembic database history.
- Generated: Partially; revisions are generated then reviewed/edited.
- Committed: Yes.
- Constraint: Never rewrite applied history; add a new revision and keep a single valid head.

**`agent-service/src/skills`:**
- Purpose: Governed executable skill packages.
- Generated: No.
- Committed: Yes.
- Constraint: The manifest, instruction, schemas, examples, and tests form one contract unit.

**`agent-service/vendor/pi-packages`:**
- Purpose: Vendored Pi package snapshot/governance input.
- Generated: Managed snapshot.
- Committed: Yes.
- Constraint: Update only with lockfile/governance verification in `agent-service/scripts`.

**`backend/storage` and `backend/uploads`:**
- Purpose: Runtime-generated user source files and visual/derivative assets.
- Generated: Yes.
- Committed: Runtime contents generally no; placeholder/fixture policy follows `.gitignore`.
- Constraint: Access only through owner-scoped backend services; never expose raw filesystem paths.

**`backend/evals` and `.quality`:**
- Purpose: Evaluation fixtures, calibration inputs, reports, and quality baselines.
- Generated: Mixed; fixtures/policies are authored, results may be generated.
- Committed: Selected reproducible inputs/baselines are committed.

**`frontend/public`:**
- Purpose: Static browser assets and PWA icons.
- Generated: No.
- Committed: Yes.

**`frontend/playwright-report`, `frontend/test-results`, `agent-service/dist`, caches, logs:**
- Purpose: Local build/test/runtime output.
- Generated: Yes.
- Committed: No; do not use as source-of-truth architecture evidence.

**`.planning`:**
- Purpose: GSD planning state and generated codebase intelligence.
- Generated: Workflow-managed.
- Committed: Yes, according to `.planning/config.json`.

**`docs`:**
- Purpose: Human-facing product, operations, and architecture documentation.
- Generated: No.
- Committed: Yes.
- Constraint: Current code/tests take precedence when documentation and implementation differ.

---

*Structure analysis: 2026-08-07*
