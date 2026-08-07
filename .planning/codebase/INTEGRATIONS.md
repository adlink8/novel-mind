---
last_mapped_commit: b679b49
---

# External Integrations

**Analysis Date:** 2026-08-07

## APIs & External Services

**Model Providers:**
- Google Cloud Vertex AI - default chat path using the Vertex `generateContent` REST API and a cached `gcloud auth print-access-token` credential (`backend/app/config.py`, `backend/app/services/vertex_gemini.py`).
  - SDK/Client: `httpx`; the Google Cloud CLI is invoked as a credential helper (`backend/requirements.txt`, `backend/app/services/vertex_gemini.py`).
  - Auth/config: `NOVELMIND_GCP_PROJECT`, `NOVELMIND_GCP_LOCATION`, optional `NOVELMIND_HTTPS_PROXY`; credential values remain external to the repository (`backend/app/config.py`).
- OpenAI, Anthropic and Google AI Studio compatible providers - selectable through LiteLLM and persisted user model configurations (`backend/app/services/ai_service.py`, `backend/app/models/ai_model.py`, `backend/app/api/models.py`).
  - SDK/Client: `litellm>=1.83.10` (`backend/requirements.txt`, `backend/app/services/ai_service.py`).
  - Auth: `NOVELMIND_OPENAI_API_KEY`, `NOVELMIND_ANTHROPIC_API_KEY`, `NOVELMIND_GEMINI_API_KEY`, or encrypted per-user model keys (`backend/app/config.py`, `backend/app/models/ai_model.py`).
- Ollama - optional local chat/embedding endpoint; embedding calls use `/api/embed` (`backend/app/config.py`, `backend/app/services/ai_service.py`).
  - SDK/Client: `httpx` for embeddings and LiteLLM-compatible model IDs for chat (`backend/app/services/ai_service.py`, `backend/requirements.txt`).
  - Auth: none detected; URL authority is `NOVELMIND_OLLAMA_BASE_URL` (`backend/app/config.py`).

**Image Generation:**
- Tencent Hunyuan via local ZCodeProxy - optional OpenAI-compatible image generation; default provider remains deterministic `mock` (`backend/app/config.py`, `backend/app/services/illustrations/hunyuan_transport.py`, `backend/app/services/illustrations/gateway.py`).
  - SDK/Client: `httpx`, provider-neutral `IllustrationTransport` (`backend/requirements.txt`, `backend/app/services/illustrations/gateway.py`).
  - Auth/config: endpoint/model/proxy use `NOVELMIND_ILLUSTRATION_BASE_URL`, `NOVELMIND_ILLUSTRATION_MODEL`, and `NOVELMIND_HTTPS_PROXY`; provider credentials are owned by the external proxy rather than browser or agent-service (`backend/app/config.py`, `backend/app/services/illustrations/hunyuan_transport.py`).

**Internal Service APIs:**
- Frontend -> FastAPI - Axios defaults to `/api`; Next.js rewrites to `BACKEND_URL` (`frontend/src/lib/api.ts`, `frontend/next.config.mjs`).
- Frontend -> agent-service - Next.js rewrites `/agent/*` directly to `AGENT_SERVICE_URL` so SSE does not traverse FastAPI (`frontend/next.config.mjs`, `agent-service/src/server.ts`).
- Agent-service -> FastAPI model gateway - OpenAI-compatible `/api/gateway/v1/chat/completions`; FastAPI remains the model/key/pricing authority (`agent-service/src/agent/provider.ts`, `backend/app/api/gateway.py`).
  - SDK/Client: Earendil Pi OpenAI-completions adapter (`agent-service/package.json`, `agent-service/src/agent/provider.ts`).
  - Auth: required `NOVELMIND_GATEWAY_TOKEN`; agent-service fails fast if absent and does not own provider keys (`agent-service/src/config.ts`, `backend/app/core/security.py`).
- Agent-service -> FastAPI tool facade - `/api/agent-tools/{tool_name}` with run-bound authorization, a 30-second timeout and 64 KiB response cap (`agent-service/src/tools/fastapi-client.ts`, `backend/app/api/agent_tools.py`).
- Agent-service -> FastAPI control plane - run creation/finalization, skill routing, approvals and queued-run polling; FastAPI owns durable state while agent sessions remain in memory (`agent-service/src/server.ts`, `agent-service/src/poller.ts`, `backend/app/api/agent.py`).

**Public Development Ingress:**
- Cloudflare Tunnel - maps the development frontend and API hostnames to local ports 3005 and 8010 (`deploy/cloudflare/config.novelmind-win.yml`, `deploy/cloudflare/README.md`).
  - Client: `cloudflared`, managed by PowerShell wrappers (`deploy/cloudflare/start-tunnel.ps1`, `deploy/cloudflare/stop-tunnel.ps1`).
  - Auth: credential file is referenced outside the repository; do not copy its contents into code or planning artifacts (`deploy/cloudflare/config.novelmind-win.yml`).

## Data Storage

**Databases:**
- PostgreSQL 16 + pgvector - authoritative users, novels, chunks, configuration, jobs, audit lineage and domain state (`docker-compose.yml`, `backend/app/models/`, `backend/migrations/`).
  - Connection: `NOVELMIND_DATABASE_URL` (`backend/app/config.py`, `backend/app/core/database.py`).
  - Client: SQLAlchemy async + asyncpg; Alembic owns schema changes (`backend/requirements.txt`, `backend/app/core/database.py`, `backend/alembic.ini`).
- ChromaDB - HTTP vector projection, with per-novel and named collections; synchronous SDK calls are wrapped with `asyncio.to_thread` (`backend/app/services/vector_store.py`).
  - Connection: code defaults to localhost:8001; CI test fixtures may override host/port through `NOVELMIND_CI_CHROMA_HOST` and `NOVELMIND_CI_CHROMA_PORT` (`backend/app/services/vector_store.py`, `backend/tests/integration/conftest.py`).
  - Client: `chromadb==1.5.9`; CI image and digest are owned by `.github/ci/service-lock.json` (`backend/requirements.txt`, `.github/ci/service-lock.json`).
- Neo4j - optional, disposable serving projection only; Compose can start Neo4j 5 Community but the application has no runtime driver and fails closed with `neo4j_driver_not_configured` (`docker-compose.yml`, `backend/app/services/relationships/projection.py`, `backend/app/services/knowledge/graph_sync.py`).

**File Storage:**
- Novel uploads use the local filesystem below `NOVELMIND_UPLOAD_DIR`, with size/containment checks in the service layer (`backend/app/config.py`, `backend/app/services/novel_service.py`).
- Illustration assets use owner/novel-scoped, content-hash paths and atomic local writes; database rows remain authoritative for metadata and references (`backend/app/services/illustrations/storage.py`, `backend/app/models/illustration.py`).
- Object storage is not implemented; the adapter remains a deployment decision (`backend/app/services/illustrations/storage.py`, `docs/DEPLOYMENT.md`).

**Caching:**
- No external cache service is detected in runtime manifests or Compose (`backend/requirements.txt`, `frontend/package.json`, `agent-service/package.json`, `docker-compose.yml`).
- Vertex access tokens are cached in process, React Query caches frontend server state, and agent-service sessions/approvals are in-memory only (`backend/app/services/vertex_gemini.py`, `frontend/package.json`, `agent-service/src/server.ts`, `agent-service/src/policy/session-approvals.ts`).

## Authentication & Identity

**Auth Provider:**
- Custom local identity - users authenticate with bcrypt password hashes and receive HS256 JWTs through Bearer tokens or HttpOnly cookies (`backend/app/core/security.py`, `backend/app/api/auth.py`, `backend/app/models/user.py`).
  - Cookie write requests enforce Origin validation; production mode validates independent JWT/encryption secrets and Secure Cookie is configuration-controlled (`backend/app/core/security.py`, `backend/app/config.py`).
  - Provider API keys are Fernet-encrypted with current/previous encryption keys for rotation and are omitted from read APIs (`backend/app/core/crypto.py`, `backend/app/models/ai_model.py`, `backend/app/api/models.py`).
  - The agent model gateway uses a separate service token; tool calls carry the end-user/per-run authorization and bind the novel ID to the run, not to model-supplied input (`backend/app/core/security.py`, `agent-service/src/agent/provider.ts`, `agent-service/src/tools/fastapi-client.ts`).

## Monitoring & Observability

**Error Tracking:**
- No configured Sentry, Datadog, New Relic, Prometheus or OpenTelemetry exporter is detected; OpenTelemetry appears only transitively in npm locks (`backend/requirements.txt`, `frontend/package.json`, `agent-service/package.json`, `agent-service/package-lock.json`).

**Logs:**
- Backend uses Python logging with third-party logger suppression and password-redacted database URLs at startup (`backend/app/core/logging.py`, `backend/app/main.py`).
- AI usage is persisted to `ai_usage_logs`; failure to write usage is intentionally non-fatal (`backend/app/services/ai_service.py`, `backend/app/models/ai_usage_log.py`).
- Provider errors in durable illustration attempts are normalized/redacted before persistence (`backend/app/services/illustrations/gateway.py`).
- CI uploads bounded JUnit, coverage, OpenAPI, integration and browser artifacts with retention policy; novel full text/upload paths are explicitly forbidden (`.github/workflows/ci.yml`, `.github/quality/baseline-policy.yml`).
- Scheduled quality failures reconcile to GitHub Issues through an isolated job with `issues:write`; PRs and untrusted checkouts cannot reach it (`.github/workflows/ci.yml`, `scripts/ci/validate-workflow.py`).

## CI/CD & Deployment

**Hosting:**
- Production hosting is not defined; the repository is explicitly local-development/security-baseline only (`docs/DEPLOYMENT.md`).
- Cloudflare Tunnel is a Windows development ingress, not a declared production deployment (`deploy/cloudflare/README.md`, `deploy/cloudflare/config.novelmind-win.yml`).
- Root Compose hosts only PostgreSQL/Chroma/optional Neo4j; application containers are not wired into the Compose topology (`docker-compose.yml`, `docs/DEPLOYMENT.md`).

**CI Pipeline:**
- GitHub Actions unified producer DAG - static audits, backend/frontend unit tests, OpenAPI compatibility, locked Postgres/Chroma integration, browser smoke, CodeQL, live/quality jobs and a stable `ci-gate` aggregate (`.github/workflows/ci.yml`).
- CI defaults to `contents: read`; secret/self-hosted/write jobs are restricted to protected events/environments and fork PRs are secretless (`.github/workflows/ci.yml`, `.github/quality/baseline-policy.yml`, `scripts/ci/validate-workflow.py`).
- Actionlint `v1.7.12`, oasdiff `v1.17.0`, pytest coverage/timeout versions and service image digests are policy-owned pins (`.github/workflows/ci.yml`, `.quality/coverage-policy.yml`, `.github/ci/service-lock.json`).
- Backend audits: Ruff, Bandit, pip-audit and CodeQL; frontend audits: TypeScript, ESLint, production-only npm audit and CodeQL (`.github/workflows/ci.yml`, `.github/codeql/codeql-config.yml`).
- No publish/release/deploy job is present in the unified workflow (`.github/workflows/ci.yml`, `docs/DEPLOYMENT.md`).

## Environment Configuration

**Required env vars:**
- Backend production: `NOVELMIND_DATABASE_URL`, `NOVELMIND_SECRET_KEY`, `NOVELMIND_ENCRYPTION_KEY`; provider-specific values are required only for the selected model path (`backend/app/config.py`).
- Provider paths: `NOVELMIND_OPENAI_API_KEY`, `NOVELMIND_ANTHROPIC_API_KEY`, `NOVELMIND_GEMINI_API_KEY`, `NOVELMIND_GCP_PROJECT`, `NOVELMIND_GCP_LOCATION`, `NOVELMIND_HTTPS_PROXY`, `NOVELMIND_OLLAMA_BASE_URL` (`backend/app/config.py`, `backend/app/services/ai_service.py`, `backend/app/services/vertex_gemini.py`).
- Agent-service: `NOVELMIND_GATEWAY_TOKEN` is mandatory; `FASTAPI_BASE_URL`, `PORT`, `POLL_ENABLED`, `POLL_INTERVAL_MS`, `POLL_CONCURRENCY`, `POLL_TIMEOUT_MS` control topology and polling (`agent-service/src/config.ts`).
- Frontend server/build: `BACKEND_URL`, `AGENT_SERVICE_URL`; browser API prefix may use `NEXT_PUBLIC_API_URL` (`frontend/next.config.mjs`, `frontend/src/lib/api.ts`).
- CI/live quality: `NOVELMIND_CI_DATABASE_URL`, `NOVELMIND_CI_DATABASE_SYNC_URL`, Chroma host/port variables, provider credentials and `RAG_SIGNING_SECRET` are referenced through workflow environments/secrets (`.github/workflows/ci.yml`, `backend/tests/integration/conftest.py`).

**Secrets location:**
- Local environment files exist but are not an authority to commit or quote: `backend/.env`, `backend/.env.example`, `frontend/.env.local`.
- GitHub Actions secrets/environments own CI live-model and signing credentials; only secret names appear in workflow configuration (`.github/workflows/ci.yml`, `.github/quality/baseline-policy.yml`).
- Persisted user provider keys are ciphertext in PostgreSQL and are decrypted only through the model property (`backend/app/models/ai_model.py`, `backend/app/core/crypto.py`).
- Cloudflare credentials are external to the repository and referenced by path only (`deploy/cloudflare/config.novelmind-win.yml`).

## Webhooks & Callbacks

**Incoming:**
- No third-party webhook receiver is detected in the FastAPI route set; integrations are request/response, polling or SSE (`backend/app/api/`, `agent-service/src/server.ts`).
- The agent-service exposes `POST /agent/novels/{novel_id}/runs` and `GET /healthz`; the run endpoint is an internal SSE service boundary, not a public provider webhook (`agent-service/src/server.ts`).

**Outgoing:**
- No third-party webhook sender is detected (`backend/app/`, `agent-service/src/`).
- Agent approval and queued-run coordination use short polling against FastAPI rather than callbacks (`agent-service/src/server.ts`, `agent-service/src/poller.ts`).

## Integration Boundaries & Risks

- FastAPI is the authority for model routing, credentials, pricing and durable artifacts; agent-service must keep logical model cost at zero and must not introduce its own provider keys or routing table (`backend/app/api/gateway.py`, `agent-service/src/agent/provider.ts`).
- The browser bypasses FastAPI for `/agent/*` SSE but the agent-service calls back into FastAPI for authorization, tools and finalization. Deployment must keep both `AGENT_SERVICE_URL` and `FASTAPI_BASE_URL` mutually reachable and consistently secured (`frontend/next.config.mjs`, `agent-service/src/config.ts`, `agent-service/src/server.ts`).
- Agent-service sessions and session approvals are in-memory. Process restart loses transient work while durable run/artifact state remains in PostgreSQL (`agent-service/src/server.ts`, `agent-service/src/policy/session-approvals.ts`, `backend/app/models/agent_runtime.py`).
- Unified CI does not install, compile, test, audit or run the agent-service governance scripts; its exact lock/permission controls currently rely on local invocation and startup enforcement (`.github/workflows/ci.yml`, `agent-service/package.json`, `agent-service/src/server.ts`).
- CI Chroma configuration has conflicting port declarations: `.github/workflows/ci.yml` and the Compose header reference 8002, while the actual mapping, service lock and integration fixture use 8001 (`.github/workflows/ci.yml`, `docker-compose.ci.yml`, `.github/ci/service-lock.json`, `backend/tests/integration/conftest.py`).
- Development Chroma uses an unpinned `latest` image even though the Python client and CI image are locked to 1.5.9 (`docker-compose.yml`, `backend/requirements.txt`, `.github/ci/service-lock.json`).
- Neo4j is present in local infrastructure without an installed application driver. Treat it as an optional rebuildable projection, never as a source of truth (`docker-compose.yml`, `backend/app/services/relationships/projection.py`, `backend/tests/contract/test_facet_readonly_contract.py`).
- Provider egress accepts encrypted user-defined base URLs only after URL allowlist/DNS/IP validation. New integrations must reuse this validation rather than call arbitrary stored URLs (`backend/app/core/url_security.py`, `backend/app/services/ai_service.py`, `backend/app/api/models.py`).
- Local upload and illustration storage have no backup/object-store integration; host loss or multi-instance deployment can orphan bytes from authoritative database rows (`backend/app/services/novel_service.py`, `backend/app/services/illustrations/storage.py`, `docs/DEPLOYMENT.md`).
- Cloudflare Tunnel points directly at local development services and `noTLSVerify` is enabled on local origins; it does not replace production TLS, network isolation, rate limiting or secret management (`deploy/cloudflare/config.novelmind-win.yml`, `docs/DEPLOYMENT.md`).

---

*Integration audit: 2026-08-07*
