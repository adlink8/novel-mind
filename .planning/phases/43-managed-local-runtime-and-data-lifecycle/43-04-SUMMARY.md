---
phase: 43-managed-local-runtime-and-data-lifecycle
plan: "04"
subsystem: desktop-runtime-recovery
tags: [desktop, runtime, recovery, fault-injection, degraded, failed, backup-restore, no-empty-success]
requires:
  - "43-02 (GraphSupervisor / process-graph / readiness / process-owner / logging)"
  - "43-03 (MigrationRunner backup-first transaction + journal retry + migrationGateFrom)"
provides:
  - "Renderer-safe runtime recovery status contract (runtime-status.ts) with stable redacted error codes"
  - "Bounded allowlisted recovery actions (retry / restart / openDiagnostics / restoreBackup) — T-43-04-02"
  - "RuntimeRecovery executor authorizing recovery actions against the runtime state machine"
  - "Reverse-order cleanup of already-started components behind a failed full start / failed migration (no half-started graph, D-43-07)"
  - "Integration fault-injection suites proving no fault is ever swallowed into empty success (D-43-09)"
affects:
  - "desktop/src/main/index.ts (future wiring point: RuntimeRecovery + bridge status events, not modified in this plan)"
  - "43-04 renderer gate (frontend RuntimeGate/RuntimeRecoveryPanel consumers of RuntimeRecoveryState — out of coordinator scope, see deviations)"
tech-stack:
  added:
    - "No new npm dependencies"
  patterns:
    - "Pure status-contract module (runtime-status.ts) shared across the main→renderer trust boundary, mirroring bridge-contract.ts"
    - "State-derived action allowlist (T-43-04-02): actions are derived from runtime state, never free-form strings"
    - "Injected data capability seam (RecoveryDataCapabilities) keeps the desktop runtime a non-data-authority (D-43-04)"
    - "Fault-injection integration suites wire real DesktopRuntime + DevelopmentProcessAdapter + MigrationRunner over FakeOps/FakeDataFs"
key-files:
  created:
    - desktop/src/shared/runtime-status.ts (188 lines)
    - desktop/src/runtime/recovery.ts (222 lines)
    - desktop/tests/runtime/recovery.test.ts (336 lines)
    - desktop/tests/runtime/runtime-lifecycle.integration.test.ts (258 lines)
    - desktop/tests/runtime/runtime-recovery.integration.test.ts (362 lines)
  modified:
    - desktop/src/runtime/desktop-runtime.ts (reverse-order cleanup behind failed start/migration)
    - desktop/src/runtime/types.ts (RECOVERY_DENIED + BACKUP_RESTORE_FAILED error codes)
    - desktop/tests/runtime/fake-process-ops.ts (DEV_ADAPTER_SPAWN_MARKERS shared map)
decisions:
  - "Every failure already reached a typed runtime state via 43-01/43-02/43-03; plan 43-04 adds (a) reverse-order teardown behind a failed full start/migration (previously components stayed half-started), and (b) the renderer-visible recovery contract + bounded action executor."
  - "Recovery actions are state-derived and allowlisted (T-43-04-02): restoreBackup is only offered in failed when a verified backup exists; in-flight and ready states offer no actions."
  - "RuntimeRecovery keeps the desktop runtime a non-data-authority: backup availability / restore are injected RecoveryDataCapabilities; restore failures map to BACKUP_RESTORE_FAILED with old data preserved."
  - "Repair from degraded is minimal (D-43-07): only the failed component is re-spawned; healthy dependents are preserved."
metrics:
  duration_minutes: 145
  completed_at: "2026-08-10"
---

# Phase 43 Plan 04: Bounded Runtime Recovery and Failure-to-Status Mapping — Summary

Turned every runtime/data failure into a visible, recoverable, typed state and proved with
integration fault injection that no fault is ever swallowed into an empty-success domain state:
a failed full start or failed migration now stops already-started components in reverse order
(no half-started graph), every failure maps to a stable redacted error code on the
renderer-safe `RuntimeRecoveryState`, and the bounded allowlisted actions (retry / restart /
openDiagnostics / restoreBackup) are the only recovery surface the renderer may request
(T-43-04-02).

## Implemented

### Renderer-safe recovery status contract (`desktop/src/shared/runtime-status.ts`)

- Pure module (no Node/Electron imports) mirroring `bridge-contract.ts`, so it can cross the
  main→renderer trust boundary. `runtimeStatusFromSnapshot(snapshot, { backupAvailable })`
  derives a `RuntimeRecoveryState`: runtime state, `ready` (true only when state is `ready` —
  never empty success, D-43-09), the failed component, the stable redacted `errorCode` /
  `errorMessage`, the bounded `recoveryActions`, `backupAvailable` and `startedAt`.
- `RECOVERY_ACTION_IDS = ["retry", "restart", "openDiagnostics", "restoreBackup"]` is the fixed
  allowlist (T-43-04-02). `recoveryActionIdsFor(state, backupAvailable)` is state-derived:
  - stopped → `[retry, openDiagnostics]`
  - starting / migrating / stopping → `[]` (in-flight, nothing is safe)
  - ready → `[]`
  - degraded → `[retry, restart, openDiagnostics]`
  - failed → `[retry, openDiagnostics]` or `[retry, openDiagnostics, restoreBackup]` when a
    verified backup exists.

### Bounded recovery executor (`desktop/src/runtime/recovery.ts`)

- `RuntimeRecovery` is the main-process authority that turns a renderer request into a typed
  lifecycle operation. It never trusts the renderer: unknown action identifiers and actions not
  in the current state's allowlist are denied with `RECOVERY_DENIED`; executed actions go
  through the runtime state machine only.
- `recover("retry")` → `ensureReady()` (full start from stopped/failed, repair from degraded);
  `recover("restart")` → `runtime.restart(failedComponent)` (targeted cascade);
  `recover("openDiagnostics")` → redacted `DiagnosticsHandle` (component labels only, no paths);
  `recover("restoreBackup")` → the injected `RecoveryDataCapabilities.restoreBackup()`, with
  failures mapped to `BACKUP_RESTORE_FAILED` (old data intact).
- The data capability is injected (D-43-04): `backupAvailable()` gates the restoreBackup action,
  `recoveryInstruction()` surfaces the bounded migration failure instruction, and the desktop
  runtime never owns backup/restore.

### No half-started graph behind failure (`desktop/src/runtime/desktop-runtime.ts`)

- `startComponents(..., tearDownOnFailure)` now stops every already-started component in reverse
  dependency order (best-effort) before a full start reports `failed`.
- A failed migration stops every started component before reporting `failed` — the runtime never
  claims readiness from a partial migration (D-43-06) and never leaves a half-running graph
  behind (D-43-07).
- The failed component itself keeps its `failed` state and redacted error (never wiped by
  cleanup), so the failure stays visible; a component that cannot be terminated stays `failed`
  (the runtime never claims a clean stop it did not achieve).
- Crash-after-ready remains `degraded` with healthy components preserved; repair is minimal
  (only the failed component is re-spawned).
- New error codes in `types.ts`: `RECOVERY_DENIED`, `BACKUP_RESTORE_FAILED`.

### Integration fault-injection suites (`desktop/tests/runtime/`)

- `recovery.test.ts` (21 tests) — status-contract unit coverage + RuntimeRecovery executor:
  allowlist derivation and boundary (unknown actions, in-flight/ready denials), honest
  ready/degraded/failed mapping, retry/restart/restore/diagnostics execution, redaction
  assertions, and the no-half-started-graph invariants (cleanup, no orphans, retry reaches
  ready, failed component error visible).
- `runtime-lifecycle.integration.test.ts` (8 tests) — real `DesktopRuntime` +
  `DevelopmentProcessAdapter` over `FakeOps`: first start to ready, targeted restart preserving
  unaffected services, full shutdown idempotent with zero live descendants, every component
  killed → degraded → typed → minimal repair → shutdown clean, occupied port →
  `START_TIMEOUT` → failed with reverse-order cleanup → retry ready, spawn failure → redacted →
  retry ready, degraded restart cascade, and state-machine-only recovery with `RECOVERY_DENIED`.
- `runtime-recovery.integration.test.ts` (8 tests) — real `MigrationRunner` wired into the real
  runtime via `migrationGateFrom` + `RecoveryDataCapabilities`: first-run migration to ready,
  failed migration → failed with old data + verified backup + typed recovery instruction,
  `restoreBackup` restoring the verified snapshot, journal-resuming retry (no re-backup),
  insufficient disk space → failed before any write with no restoreBackup offered, denied
  app-data write → failed with data preserved, interrupted migration resuming across runtime
  instances, and a three-fault matrix asserting honest failed terminals.
- `fake-process-ops.ts` gained `DEV_ADAPTER_SPAWN_MARKERS`, the canonical spawn markers for each
  component in the development adapter (pgvector/chroma/uvicorn/start.mjs/next), so kill-fault
  injection addresses the right spawned process.

## Verification (all executed, twice)

1. `cd desktop && npm run typecheck` — exit 0, no errors (both passes).
2. `cd desktop && npx playwright test --config tests/runtime/playwright.config.ts` — **113 passed /
   0 failed** both passes (~11s). Breakdown: adapter-contract 24, state-machine 20,
   process-graph 21, process-tree 11, recovery 21, runtime-lifecycle.integration 8,
   runtime-recovery.integration 8. Pre-plan baseline was 76; this plan adds **37 tests**.
3. `cd desktop && npx playwright test --config tests/data/playwright.config.ts` — **27 passed**
   both passes (regression; the wiring change touches the same migration path).
4. `cd desktop && npx playwright test` (full shell regression, Electron + renderer) — **56 passed**
   both passes, no regression from the runtime changes.
5. `git status --short` — new/changed files are exactly `desktop/src/runtime/`,
   `desktop/src/shared/`, `desktop/tests/runtime/` and `.planning/phases/43-*/`; all pre-existing
   user modifications (`.gitignore`, `.planning/config.json`, `deploy/`, `frontend/next-env.d.ts`,
   `backend/`, `scripts/`) are untouched.

## Deviations from Plan

### Auto-fixed Issues (all test-side, deterministic)

**1. [Rule 1 - Bug] Kill-fault injection addressed the wrong process**
- **Found during:** Task 2 integration run.
- **Issue:** `ops.spawnedProcess("fastapi")` etc. matched nothing because the dev adapter spawns
  `uvicorn` / `start.mjs` / `pgvector/pgvector` / `chromadb/chroma` — the marker must be the
  spawn string, not the component id. This surfaced as 8 deterministic failures.
- **Fix:** added `DEV_ADAPTER_SPAWN_MARKERS` (component → spawn marker) to `fake-process-ops.ts`
  and used it in every kill-fault test.
- **Files modified:** `desktop/tests/runtime/fake-process-ops.ts`,
  `desktop/tests/runtime/recovery.test.ts`,
  `desktop/tests/runtime/runtime-lifecycle.integration.test.ts`.

**2. [Rule 1 - Bug] Tests asserted `failed` before ever starting the runtime**
- **Found during:** Task 2 integration run.
- **Issue:** `recovery.status()` on a never-started runtime correctly reports `stopped`; several
  tests asserted `failed` without first calling `ensureReady()` with an injected fault.
- **Fix:** each such test now drives the runtime to `failed` (or `degraded`) first.
- **Files modified:** `desktop/tests/runtime/recovery.test.ts`.

**3. [Rule 1 - Bug] Repair-from-degraded spawn count expected the transitive cascade**
- **Found during:** Task 2 integration run.
- **Issue:** `repairGraph` is minimal by design (D-43-07) — only the failed component is
  re-spawned — but the test expected the whole affected cascade to restart.
- **Fix:** assert exactly `+1` spawn on repair (healthy dependents preserved).
- **Files modified:** `desktop/tests/runtime/runtime-lifecycle.integration.test.ts`.

### Scope deviations (coordinator-directed, documented)

- The plan listed `frontend/src/components/desktop/RuntimeGate.tsx`,
  `RuntimeRecoveryPanel.tsx`, `RuntimeGate.test.tsx` and `desktop/tests/integration/*.spec.ts`
  (Electron E2E). The coordinator's scope for this wave is **desktop-only** with new tests under
  `desktop/tests/runtime/` running on the self-contained runtime playwright config; `desktop/src/
  main/index.ts`, the frontend renderer gate, and `frontend/` were NOT modified (also protected
  by the user's uncommitted changes in the working tree). The desktop-side recovery contract
  (`runtime-status.ts` + `runtime/recovery.ts`) is exactly what the future renderer gate
  consumes; the wiring of `RuntimeRecovery` into `desktop/src/main/index.ts` and the renderer
  gate component remain future 43-04 renderer work.
- The integration suites live at `desktop/tests/runtime/*.integration.test.ts` instead of
  `desktop/tests/integration/*.spec.ts` so they run under the pure-unit runtime config (no
  Electron/globalSetup), matching the existing 43-01/43-02/43-03 pattern.

## Known Stubs

None. `RecoveryDataCapabilities` is an injected seam exercised by real `MigrationRunner`
adaptation; the DB/vector/app-metadata migration steps remain injected by design (the desktop
runtime is not a database authority, D-43-04).

## Threat Flags

None beyond the plan's register. The new surface is the recovery action channel: `RuntimeRecovery`
denies unknown/out-of-state actions (`RECOVERY_DENIED`) and executes only through the runtime state
machine (T-43-04-02 mitigation), and the status contract carries only stable redacted codes
(T-43-04-01 mitigation). No new network/file/process surface was added.

## Self-Check: PASSED

- 5 files created, 3 files modified — all under `desktop/src/` and `desktop/tests/runtime/`
  plus `.planning/phases/43-*/` (verified by `git status --short`).
- `npm run typecheck` clean; runtime suite 113/113, data suite 27/27, shell regression 56/56,
  each twice.
- No commits created (orchestrator-owned); no files outside the declared scope were touched.
