# Phase 43: Managed Local Runtime and Data Lifecycle - Context

**Gathered:** 2026-08-09
**Status:** Ready for planning
**Source:** User-confirmed v1.5 desktop direction

<domain>
## Phase Boundary

Implement the process and data lifecycle beneath the secure shell. This phase makes local services deterministic and recoverable without moving NovelMind domain authority into Electron.

</domain>

<decisions>
## Implementation Decisions

### Deep runtime module

- **D-43-01:** `DesktopRuntime` is a small deep interface exposing only `ensureReady`, `status`, `restart` and `shutdown` with typed lifecycle/error states.
- **D-43-02:** `PackagedProcessAdapter` and `DevelopmentProcessAdapter` implement the same contract; Electron main depends on the interface rather than process details.
- **D-43-03:** The managed graph includes Next, FastAPI, Agent Service, PostgreSQL and vector storage with explicit dependency order, readiness probes, dynamic endpoint allocation and process-tree ownership.
- **D-43-04:** Electron coordinates runtime lifecycle only; backend/database services remain factual, domain and persistence authorities.

### Data lifecycle

- **D-43-05:** Mutable data, logs, migration state and backups live under a versioned `%APPDATA%/NovelMind` layout; installed application resources remain immutable.
- **D-43-06:** First run and compatible upgrade are idempotent. Migration begins from a recoverable backup and never reports ready after a partial failure.
- **D-43-07:** Shutdown drains or terminates the owned process tree without orphaned services; targeted restart preserves unaffected services when safe.

### Failure behavior

- **D-43-08:** Startup, readiness, crash, port, migration and dependency failures surface typed degraded/failed states with bounded repair actions and diagnostic logs.
- **D-43-09:** Runtime failure must never be rendered as an empty successful library, novel or analysis result.

### the agent's Discretion

- Choose concrete state-machine names, process supervisor primitives and health-check intervals.
- Select the bundled PostgreSQL/vector topology proven feasible by Phase 41.
- Define backup retention defaults and bounded diagnostic log rotation.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

- `.planning/phases/41-electron-architecture-and-packaging-proof/41-CONTEXT.md` - bundled-runtime and GO/NO-GO contract.
- `.planning/phases/42-secure-desktop-shell/42-CONTEXT.md` - Electron ownership and bridge boundary.
- `.planning/ROADMAP.md` - Phase 43 success criteria.
- `backend/app/main.py` - FastAPI composition and lifecycle entrypoint.
- `agent-service/src` - Agent Service runtime entrypoints and health behavior.
- `docker-compose.yml` - current PostgreSQL/vector service assumptions.
- `backend/alembic.ini` - current migration entrypoint.
- `https://www.electronjs.org/docs/latest/api/utility-process` - Electron-managed Node child process option.

</canonical_refs>

<specifics>
## Specific Ideas

- Make readiness a dependency-aware state machine rather than sleeps or port-only checks.
- Store a machine-readable runtime snapshot containing component version, endpoint, PID ownership, health and last failure.
- Exercise kill/restart, corrupt/incomplete migration and occupied-port cases in automated tests.

</specifics>

<deferred>
## Deferred Ideas

- Renderer credential delivery and provider offline policy are Phase 44.
- Installer upgrade/uninstall policy qualification is Phase 45.
- Cloud sync, multi-machine replication and automatic remote backup are outside v1.5.

</deferred>

---

*Phase: 43-managed-local-runtime-and-data-lifecycle*
*Context gathered: 2026-08-09*
