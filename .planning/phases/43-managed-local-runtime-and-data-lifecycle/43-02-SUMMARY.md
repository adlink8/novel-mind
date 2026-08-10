---
phase: 43-managed-local-runtime-and-data-lifecycle
plan: "02"
subsystem: desktop-runtime
tags: [desktop, runtime, process-graph, readiness, port-allocation, process-tree, logging]
requires:
  - "43-01 (DesktopRuntime deep module + dual process adapters)"
provides:
  - "Five-component dependency graph supervisor (topological order, cycle detection, restart cascade)"
  - "OS-allocated dynamic loopback port pool (fixed ports rejected by construction)"
  - "Component-specific protocol readiness probes (PG SQL, chroma heartbeat, fastapi health+deps, agent /healthz, next HTTP)"
  - "Instance-bound Windows process-tree ownership with drain-then-kill, never name-matched"
  - "Rotated, redacted component log sinks under %APPDATA%/NovelMind/logs/{component}"
  - "Dynamic Chroma host/port env injection for backend vector_store.py (blocks the fixed-port failure)"
affects:
  - "43-03 (migration orchestration, app-data layout, runtime snapshot)"
  - "desktop/src/main/index.ts (runtime interface wiring — renderer URL resolved through the managed graph)"
tech-stack:
  added:
    - "No new npm dependencies"
  patterns:
    - "Pure graph-analysis module (ProcessGraph) separated from the concrete supervisor (GraphSupervisor)"
    - "Protocol-level readiness via injectable transports (PostgreSQL v3.0 wire client included)"
    - "Instance-bound ownership: processes are killed by registered PID, never by executable name"
    - "Injectable ProcessOperations seam reused for the process owner (FakeOps in tests)"
key-files:
  created:
    - desktop/src/runtime/port-allocator.ts (138 lines)
    - desktop/src/runtime/readiness.ts (307 lines)
    - desktop/src/runtime/process-graph.ts (330 lines)
    - desktop/src/runtime/process-owner.ts (110 lines)
    - desktop/src/runtime/logging.ts (182 lines)
    - desktop/tests/runtime/process-graph.test.ts (352 lines)
    - desktop/tests/runtime/process-tree.windows.test.ts (193 lines)
  modified:
    - desktop/src/runtime/process-operations.ts (allocateLoopbackPort delegates to port-allocator)
    - desktop/src/runtime/development-process-adapter.ts (fastapi launch injects dynamic DATABASE_URL + vector host/port)
    - desktop/src/main/index.ts (runtime wiring: renderer resolved through DesktopRuntime, owned shutdown on quit)
    - backend/app/services/vector_store.py (env injection for Chroma host/port)
decisions:
  - "FastAPI readiness requires its dependency chain (postgres_pgvector + vector_store endpoints present) AND /api/health 200 — never port-open alone (D-43-03, T-43-02-03)."
  - "PostgreSQL readiness is a real SQL roundtrip over a minimal v3.0 wire client (SELECT 1); any auth method other than trust/no-password is reported NOT ready rather than probed blindly."
  - "The Chroma hard-coded port block was resolved with NOVELMIND_VECTOR_HOST/NOVELMIND_VECTOR_PORT env injection in vector_store.py (constructor defaults unchanged), so all three VectorStore() call sites keep working and dynamic ports are expressible."
  - "The dev adapter injects the dynamic Postgres URL via NOVELMIND_DATABASE_URL (config.py already reads NOVELMIND_-prefixed env), so the backend connects to the OS-allocated port."
  - "Process ownership is instance-bound: a PID joins the owned tree only by register(); terminate() never matches executable names, so unrelated user processes always survive."
  - "Electron main resolves the renderer URL through DesktopRuntime (one runtime interface, D-43-02) but keeps NOVELMIND_RENDERER_URL as a hermetic override for shell tests; a not-ready graph throws READY_INVARIANT_VIOLATION instead of rendering against a broken runtime (D-43-09)."
  - "GraphSupervisor (the 43-02 coordinator) fails closed on the first component that cannot start or cannot reach strict readiness, stopping already-started components in reverse order before returning a typed failed result."
metrics:
  duration_minutes: 95
  completed_at: "2026-08-10"
---

# Phase 43 Plan 02: Process Graph, Dynamic Ports, Protocol Readiness, Process-Tree Ownership, Logging — Summary

Built the five-component dependency graph supervisor, OS-allocated dynamic loopback port pool,
component-specific protocol readiness probes (PostgreSQL SQL, Chroma heartbeat, FastAPI health with
dependency chain, Agent `/healthz`, Next HTTP), instance-bound Windows process-tree ownership, and
rotated/redacted component log sinks under `%APPDATA%/NovelMind/logs`, and removed the Chroma
hard-coded-port blocker by adding env injection to `backend/app/services/vector_store.py`.

## Implemented

### Process graph and startup order (`process-graph.ts`)

- `ProcessGraph`: pure analysis over the canonical five components — topological order, cycle
  detection, transitive restart-cascade sets, dependency satisfaction, fail-closed validation
  (unknown dependency / missing component / cycle rejected before any process starts).
- `GraphSupervisor`: the concrete coordinator. Starts components strictly in dependency order,
  gates every dependent start on its dependency chain being present, applies the protocol-level
  readiness probes with a bounded deadline, and on any failure stops the already-started
  components in reverse order before returning a typed `{ ok: false, failed }` result — never
  ready while a mandatory component is degraded/failed (D-43-08).

### Dynamic port allocation (`port-allocator.ts`)

- `allocateLoopbackPort()` asks the OS for a free loopback port (topology.ts contract: port 0 =
  OS allocation); fixed nonzero ports are rejected by `assertDynamicPort`/`isDynamicPort`.
- `PortPool` guarantees mutually distinct ports per runtime instance for "dynamic non-conflicting
  endpoints". `nodeProcessOperations.allocateLoopbackPort` now delegates here so adapters and
  graph share one allocation source.

### Protocol-level readiness (`readiness.ts`)

| Component | Probe |
|---|---|
| postgres_pgvector | PostgreSQL v3.0 wire handshake + `SELECT 1` roundtrip |
| vector_store | `GET /api/v2/heartbeat` → 200 |
| fastapi | dependency chain (postgres + vector present) AND `GET /api/health` → 200 |
| agent_service | `GET /healthz` → 200 |
| next | `GET /` → 200 |

- Transports are injectable (`httpStatus` + `postgresReady`); `nodeReadinessTransport()` is the
  production implementation including a minimal Postgres wire client (startup, auth-ok, query,
  ReadyForQuery, terminate). Demanding auth methods report NOT ready, never probe blindly.
- `waitForReadiness` polls with an explicit deadline — bounded retries, no sleeps.

### Process-tree ownership (`process-owner.ts`)

- Instance-bound: only processes registered for a `RuntimeComponent` are ever killed; never by
  executable name (T-43-02-01). `terminate` drains (graceful kill + wait) then force-kills the
  whole tree (`taskkill /T /F`) within bounded budgets; a tree that survives both surfaces
  `ProcessOwnerError` and ownership is retained so the caller cannot claim a clean stop.

### Log sinks (`logging.ts`)

- `ComponentLogger` writes to `<appDataRoot>/logs/{component}/{component}.log` (default
  `%APPDATA%/NovelMind`), rotates at a size cap (default 1 MiB) keeping at most `maxFiles`
  rotated files, and redacts every line (Bearer, JWT/opaque tokens, `sk-*` keys, `AIza*` Google
  keys, `key=value` and `UPPER_SNAKE_*_PASSWORD=`-style secrets) before it touches disk
  (T-43-02-02).

### Backend blocker fix (`vector_store.py`)

- `VectorStore.__init__` now reads `NOVELMIND_VECTOR_HOST` / `NOVELMIND_VECTOR_PORT` (constructor
  defaults `localhost`/`8001` unchanged), so the singleton and both other `VectorStore()` call
  sites keep working while the runtime can inject the OS-allocated Chroma port. This is the
  minimal change required for dynamic ports to work (the deterministic blocker identified in
  preflight).

### Runtime wiring (`desktop/src/main/index.ts`)

- The shell now owns ONE runtime interface (D-43-02): `runtimeInstance()` builds a
  `DesktopRuntime` over `DevelopmentProcessAdapter`. `ensureRendererUrl()` resolves the renderer
  URL from the runtime snapshot (`next` endpoint) when no `NOVELMIND_RENDERER_URL` override is
  set; a not-ready graph throws `READY_INVARIANT_VIOLATION` instead of rendering against a broken
  runtime (D-43-09). `will-quit` drains the owned runtime best-effort.
- The dev adapter injects the dynamic Postgres URL (`NOVELMIND_DATABASE_URL`, read by
  `backend/app/config.py`) and the dynamic vector host/port (`NOVELMIND_VECTOR_HOST/PORT`) into
  the fastapi launch.

## Verification (all green)

- `cd desktop && npx tsc --noEmit` — exit 0.
- `cd desktop && npx playwright test --config tests/runtime/playwright.config.ts` — **76 passed**
  (43-01 state-machine + adapter-contract suites and the new process-graph + process-tree suites).
- `cd desktop && npx playwright test` — **56 passed** (Electron shell smoke + security suites; no
  regression from the main-process wiring).
- `cd backend && venv/Scripts/python.exe -m py_compile app/services/vector_store.py` — OK.

## Deviations from Plan

None — the plan executed as written. Two in-scope notes:

- The "starts the graph strictly in dependency order" test initially matched the next binary by a
  forward-slash marker that `path.join` turns into backslashes on Windows; the test normalizes
  separators now. (Test-only fix.)
- The no-orphans test initially expected 3 spawns for a fastapi→agent failure; agent_service is
  itself spawned before failing strict readiness, so the correct count is 4. (Test expectation
  corrected; the supervisor stops that tree too.)

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat register — the new network surface is limited to loopback
readiness probes; log redaction (T-43-02-02) and instance-bound ownership (T-43-02-01) are
implemented as designed.

## Self-Check: PASSED
