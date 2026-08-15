---
phase: 43-managed-local-runtime-and-data-lifecycle
plan: "01"
subsystem: desktop-runtime
tags: [desktop, runtime, state-machine, adapter, process-lifecycle, electron]
requires:
  - "42-02 (secure desktop shell IPC/security boundary)"
provides:
  - "DesktopRuntime deep lifecycle module (ensureReady/status/restart/shutdown)"
  - "Shared adapter contract suite + dual adapters (development/packaged)"
  - "Typed runtime state machine + fail-closed packaged boundary"
affects:
  - "43-02 (protocol-level readiness; migration orchestration; app-data layout)"
  - "desktop/src/main/index.ts (Phase 43 wiring point, not yet modified)"
tech-stack:
  added:
    - "No new npm dependencies (Playwright already present in desktop/)"
  patterns:
    - "Deep module with 4-method public surface and explicit transition table"
    - "Contract-first adapters over injected process operations (FakeOps seam)"
    - "Fail-closed packaged adapter (approved bundled paths only)"
key-files:
  created:
    - desktop/src/runtime/types.ts (220 lines)
    - desktop/src/runtime/desktop-runtime.ts (350 lines)
    - desktop/src/runtime/base-process-adapter.ts (266 lines)
    - desktop/src/runtime/development-process-adapter.ts (186 lines)
    - desktop/src/runtime/packaged-process-adapter.ts (86 lines)
    - desktop/src/runtime/process-operations.ts (160 lines)
    - desktop/tests/runtime/adapter-contract.test.ts (249 lines)
    - desktop/tests/runtime/state-machine.test.ts (327 lines)
    - desktop/tests/runtime/fake-process-ops.ts (143 lines)
    - desktop/tests/runtime/playwright.config.ts (21 lines)
  modified:
    - (none; plan executed on new files only)
decisions:
  - "State machine uses an explicit transition table with an exported canTransition guard; illegal transitions are rejected, not coerced."
  - "ready is only entered after asserting every component is ready; ready-with-failed-dependency is unrepresentable and guarded by READY_INVARIANT_VIOLATION."
  - "Migration is injected (MigrationGate), never owned: DesktopRuntime coordinates lifecycle only, staying a non-domain authority."
  - "PackagedProcessAdapter launches only the Phase 41 approved bundled Next-standalone path and fails closed (UNSUPPORTED_IN_PACKAGED) for bundled Python/PG/vector; no PATH/Docker fallback exists."
  - "DevelopmentProcessAdapter reuses existing dev entrypoints but always allocates dynamic loopback ports and injects them (fixed dev ports 8010/3005/3100/5432/8001 are never hard-coded)."
  - "Readiness is probe-based (tcp/http against the allocated endpoint), never port-open alone (D-43-03); protocol-level probing is deferred to 43-02."
  - "Shutdown on Windows is drain (kill + wait) then process-tree force-kill (taskkill /T /F); a tree that cannot die is reported as failed, never as cleanly stopped."
metrics:
  duration_minutes: 47
  completed_at: "2026-08-10"
---

# Phase 43 Plan 01: DesktopRuntime Deep Module and Dual Process Adapters — Summary

Defined the stable `DesktopRuntime` deep module (4-method surface, typed 7-state machine) and the
contract-equivalent `DevelopmentProcessAdapter` / `PackagedProcessAdapter` pair, verified by one
shared adapter-contract suite plus a state-machine suite, with a fail-closed packaged boundary
that honestly records the Phase 41 NO-GO limits.

## Implemented

### DesktopRuntime deep module (`desktop/src/runtime/desktop-runtime.ts`, `types.ts`)

- Public surface is exactly four methods (D-43-01): `ensureReady(): Promise<RuntimeSnapshot>`,
  `status(): Promise<RuntimeSnapshot>`, `restart(target?: RuntimeComponent): Promise<RuntimeSnapshot>`,
  `shutdown(): Promise<ShutdownReport>`. The public exports of `types.ts` carry only the four lifecycle
  methods (plus vocabulary types); no domain operations are exposed.
- State machine `stopped | starting | migrating | ready | degraded | failed | stopping` with an
  explicit, total transition table (`CAN_TRANSITION`), exported `canTransition(from, to)` guard, and
  no self-loops. Illegal transitions throw `ILLEGAL_TRANSITION`; the state-machine test enumerates all
  7 x 7 pairs and asserts the table exactly.
- `ready` is only entered after asserting every component is ready (`READY_INVARIANT_VIOLATION`
  guard); "ready with a failed dependency" is unrepresentable. `stopping -> failed` when a process
  tree cannot be terminated (honest — an orphan may remain).
- Snapshots (`RuntimeSnapshot` / `ComponentSnapshot`) carry component state, readiness and allocated
  loopback endpoints only — no secrets, no PIDs, no executables, no command lines. Errors are redacted
  to a stable code plus a fixed literal message. A test serializes a ready snapshot and asserts the
  absence of secret/PID/executable keywords.
- Runtime crash handling: unintentional child exit after ready -> component `failed`, runtime
  `degraded`; `ensureReady()` from degraded repairs the non-ready components; restart from degraded
  rebuilds only what is needed. Whole-graph and targeted restarts (target + transitive dependents,
  D-43-07) are implemented; unaffected services are preserved.
- Migration is injected via `MigrationGate` (default = no migration) — never owned; migration failure
  -> `failed` with `MIGRATION_FAILED`, never `ready`.
- No backend domain modules are imported anywhere in `desktop/src/runtime/` (static scan below).
- Wiring point kept intact: `desktop/src/main/index.ts` is untouched; 43-02+ wires the runtime in.

### Dual process adapters (D-43-02)

- Shared abstract `BaseProcessAdapter` owns the process/endpoint maps, idempotent start, readiness
  probing, drain-then-kill shutdown and unintentional-exit notifications. PID/executable internals
  stay private; the runtime only ever sees typed endpoints.
- All process operations (spawn/exit/kill/probe/port allocation) flow through an injected
  `ProcessOperations` seam (`process-operations.ts`); tests use `FakeOps` so the contract suite
  exercises spawn failure, early exit, never-ready, drain hang and kill failure deterministically.
- `DevelopmentProcessAdapter` (mode `development`) launches all five components via existing dev
  entrypoints: backend venv uvicorn (`app.main:app` on a dynamic loopback port), Next `next dev`
  (dynamic port), `agent-service/start.mjs` (PORT env + `FASTAPI_BASE_URL` to the dynamic backend
  endpoint), and the docker-compose images `pgvector/pgvector:pg16` and `chromadb/chroma` — always
  with an OS-allocated loopback port injected (`-p <port>:<container>` / `--port` / `--port` / `PORT`),
  never the fixed dev ports.
- `PackagedProcessAdapter` (mode `packaged`) launches ONLY the Phase 41 approved bundled path: Next
  standalone `server.js` via the Electron-embedded Node (`ELECTRON_RUN_AS_NODE=1`, proven in
  `desktop/proof/bundled-node-evidence.json`). All other components fail closed with
  `UNSUPPORTED_IN_PACKAGED` before the spawn seam — there is no PATH or Docker fallback
  (T-43-01-01). The contract suite asserts that the unlaunchable set never reaches `ops.spawn`.

### Tests

- `desktop/tests/runtime/adapter-contract.test.ts` — one suite run against BOTH adapters (24 tests:
  12 per adapter) covering mode/launchable sets, safe `describe()` labels, idempotent start with
  dynamic loopback endpoints, fail-closed unlaunchable components (packaged), missing-executable
  (`EXECUTABLE_NOT_FOUND`), spawn failure (`SPAWN_FAILED`), early exit (`EXIT_EARLY`), readiness
  timeout (`START_TIMEOUT` + cleanup), drain-then-kill shutdown, kill failure
  (`STOP_KILL_FAILED` + ownership retained), and unintentional-exit notification semantics.
- `desktop/tests/runtime/state-machine.test.ts` — 20 tests: transition-table enumeration (all 7x7
  pairs, self-loop free, exact match to the canonical table), terminal-state enumeration
  (stopped/failed outgoing edges), ready-reachability sources, illegal-path rejection, plus
  end-to-end lifecycle flows (ready, idempotency, snapshot redaction, startup failure, recovery,
  migration, crash->degraded->repair, targeted/whole-graph restart, shutdown idempotency,
  restart-after-shutdown, unknown target, failed shutdown).

## Verification (all executed)

1. `cd desktop && npm run typecheck` — PASS (0 errors).
2. `cd desktop && npx playwright test --config tests/runtime/playwright.config.ts` — PASS, 44/44
   (24 adapter-contract + 20 state-machine), ~4s, via the existing Playwright runner (no vitest).
3. State-machine transition enumeration — PASS (7x7 pairs asserted; terminal outgoing edges asserted
   per terminal).
4. Static authority-boundary scan — PASS: `rg "backend/app/(models|services|api)|NovelService|ChapterService" desktop/src/runtime/`
   returns no matches (exit 1); an import-statement-only scan also finds nothing.
5. `desktop/tests/runtime/playwright.config.ts` is a self-contained config (no Electron/globalSetup)
   so the unit suites do not disturb the existing shell-smoke config.
6. `npx tsc -p tsconfig.build.json` compiles clean and emits `dist/runtime/*` (the globalSetup build
   path is unaffected).
7. `git status --short` — the only new paths are `desktop/src/runtime/` and `desktop/tests/runtime/`.
   All pre-existing user modifications (`.gitignore`, `.planning/config.json`, `backend/app/config.py`,
   `deploy/`, `frontend/next-env.d.ts`, `scripts/*`) are untouched.

## Deviations from Plan

- **[Documented] 41 NO-GO boundary handling (per orchestrator instructions):** Task 2 originally
  said "packaged mode uses only Phase 41 approved bundled paths" and the execution instructions
  require fail-closed placeholder behavior for the unproven bundled components. Implemented exactly
  that: `PackagedProcessAdapter.launchable = ["next"]`; `postgres_pgvector`, `vector_store`, `fastapi`
  and `agent_service` reject with `UNSUPPORTED_IN_PACKAGED` (a NEW stable error code added to
  `RUNTIME_ERROR_CODES`) and never reach the spawn seam. There is no PATH/Docker fallback anywhere.
- **[Rule 1 - Bug] No implementation bugs found during execution.** Three initial test failures were
  incorrect test expectations (packaged launchable set, dev absolute-command component choice, and
  per-terminal outgoing edges), fixed in the tests; the implementation behavior was correct.
- **[Rule 2] Added `RUNTIME_ERROR_CODES.BUSY` and `COMPONENT_UNKNOWN`** — stable codes needed by the
  restart/shutdown contract (rejecting restart/shutdown while stopping, unknown restart target) that
  were not enumerated in the plan but are required for typed degraded/failed behavior (D-43-08).

## Packaged fail-closed boundary (recorded)

Phase 41 recorded NO-GO for the full packaged runtime: only the Next-standalone + Electron-embedded
Node path is proven (`desktop/proof/bundled-node-evidence.json`). In keeping with that honest record,
`PackagedProcessAdapter` expresses all five components in the shared component vocabulary but only the
`next` component has an approved launch path; the other four fail closed. A packaged-mode `DesktopRuntime`
therefore cannot reach `ready` (its `next` alone starting leaves the graph degraded), which is the
correct behavior until bundled Python / native PG / vector-store prerequisites land in a later plan.
Wiring the runtime into `desktop/src/main/index.ts` is deliberately deferred to 43-02 (the plan's
`key_links` contract), keeping the 42 shell intact.

## Known Stubs

None. All components express real launch behavior; unproven packaged paths are intentional fail-closed
code paths (not placeholders), tracked by this SUMMARY.

## Self-Check: PASSED

- 10 new files created under `desktop/src/runtime/` and `desktop/tests/runtime/` — verified by
  `git status --short` and `wc -l`.
- `npm run typecheck` clean; `tsc -p tsconfig.build.json` clean.
- `npx playwright test --config tests/runtime/playwright.config.ts` — 44 passed / 0 failed.
- Static scan (plan verify pattern) — no backend domain imports.
- No commits created (orchestrator-owned); no files outside `desktop/src/runtime/`,
  `desktop/tests/runtime/`, `.planning/phases/43-*/` were touched.
