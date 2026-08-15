---
phase: 44-desktop-transport-credentials-and-offline-behavior
plan: "01"
subsystem: dynamic-transport-bootstrap
tags: [desktop, bootstrap, endpoint-resolver, session, loopback, sse, api-client, no-fixed-ports]
requires:
  - "43-01 (DesktopRuntime deep module + RuntimeSnapshot with component endpoints)"
  - "43-02 (GraphSupervisor dynamic OS-allocated loopback endpoints + port-allocator)"
  - "43-04 (ready invariant: ready only when every component ready — D-43-09)"
  - "42-01/42-03 (DesktopBridge + getBootstrap capability + renderer capabilities resolver)"
provides:
  - "Typed one-session runtime bootstrap contract (bootstrap-contract.ts) with dynamic loopback endpoints + session id"
  - "Main-owned RuntimeBootstrapProvider (bootstrap.ts) — ready-only, deterministic expiry/rotation, loopback-validated, no secrets"
  - "Single logical-service endpoint seam (endpoint-resolver.ts) routing HTTP + SSE through one resolver"
  - "DesktopBootstrap extended with the one-session `runtime` payload (bridge + IPC schema)"
affects:
  - "44-02 (local-auth session tokens will consume the bootstrap session; renderer still never sees credentials)"
  - "44-03 (SSE reconnect/cancellation tests consume the resolver + session bootstrap)"
  - "frontend/src/components/export/export.ts — separate NEXT_PUBLIC_API_URL consumer (out of scope, static scan noted it)"
tech-stack:
  added:
    - "No new npm dependencies"
  patterns:
    - "Pure shared contract module (bootstrap-contract.ts) crossing main→renderer boundary (mirrors bridge-contract.ts / runtime-status.ts)"
    - "Type-only desktop imports in the web bundle; local mirror of allowlist logic where a value import would leak desktop code (build-boundary pattern)"
    - "Main-owned runtime bootstrap derived from the runtime snapshot; renderer resolves logical services only (never port logic)"
    - "Deterministic session rotation: session id + issuedAt change on restart and expiry"
key-files:
  created:
    - desktop/src/shared/bootstrap-contract.ts (71 lines)
    - desktop/src/runtime/bootstrap.ts (177 lines)
    - desktop/tests/runtime/bootstrap.test.ts (288 lines)
    - frontend/src/lib/runtime/endpoint-resolver.ts (127 lines)
    - frontend/src/lib/runtime/__tests__/endpoint-resolver.test.ts (226 lines)
  modified:
    - desktop/src/shared/bridge-contract.ts (DesktopBootstrap.runtime: RuntimeBootstrap | null)
    - desktop/src/main/index.ts (session bootstrap provider wired into getBootstrap + restart/quitting invalidation)
    - desktop/src/main/ipc/bridge-schema.ts (getBootstrap response shape doc)
    - frontend/src/lib/api/client.ts (async resolver baseURL interceptor)
    - frontend/src/lib/sse.ts (desktop agent origin prepend)
    - frontend/src/lib/desktop/capabilities.test.ts + frontend/src/components/desktop/__tests__/RuntimeGate.test.tsx (bridge fixtures now carry runtime: null)
    - frontend/src/components/desktop/RuntimeRecoveryPanel.tsx (pre-existing build break fix — see deviations)
decisions:
  - "Runtime endpoints are session bootstrap data, never fixed build-time public env (D-44-01/D-44-02): the resolver reads the typed session from the bridge; browser mode falls back to the existing relative rewrite routes."
  - "Bootstrap is one-session: session id + issuedAt rotate on runtime restart and on expiry (1h TTL); a long-lived renderer never dials a stale session."
  - "The endpoint resolver consults the bridge on every resolve (rotation detection) and caches only the endpoint computation per session id; a stale cached session is never served (44-01 acceptance: stale bootstrap fails with typed unavailable)."
  - "RuntimeBootstrap carries NO provider key / raw env / process path — only approved loopback endpoints + bounded session metadata (T-44-01-02)."
  - "`runtime` is null on the bridge until the managed runtime is fully ready (D-43-09); the renderer resolves unavailable(not-ready) and keeps the browser relative baseURL — fail closed, never a guessed URL."
metrics:
  duration_minutes: 120
  completed_at: "2026-08-10"
---

# Phase 44 Plan 01: Dynamic Endpoint Bootstrap and Single Endpoint Resolver — Summary

Built the dynamic endpoint/bootstrap contract and routed the frontend HTTP + SSE transports through a
single logical-service resolver: the desktop main process now derives a typed one-session bootstrap
(dynamic loopback endpoints for all five components + session id + expiry) from the managed runtime
snapshot and exposes it via the existing `getBootstrap` bridge capability; the renderer resolves
`apiBaseUrl` / `agentBaseUrl` through `RuntimeEndpointResolver` in Electron mode and falls back to the
existing relative rewrite routes in browser dev. No runtime endpoint is frozen into a build-time
`NEXT_PUBLIC_*` variable and the renderer never receives secrets, env or paths (D-44-01/D-44-02,
T-44-01-01/T-44-01-02).

## Implemented

### Desktop: typed one-session bootstrap (`desktop/src/shared/bootstrap-contract.ts` + `desktop/src/runtime/bootstrap.ts`)

- `BootstrapSession` — one-session payload: `sessionId` (rotates on restart and expiry), `issuedAt`,
  `expiresAt` (1h TTL, `BOOTSTRAP_SESSION_TTL_MS`), loopback endpoints for all five components and the
  three logical handles (`services.api` → fastapi, `services.agent` → agent_service, `services.renderer`
  → next), and bounded `capabilities` (agentStreaming).
- `RuntimeBootstrap = { status: "ready"; session } | { status: "unavailable"; reason: "not-ready" | "expired" | "invalidated" | "malformed" }`.
- `RuntimeBootstrapProvider` (pure Node, no Electron) derives the session from a `RuntimeSnapshot`:
  - **cannot be created before ready** — null runtime or any non-ready snapshot → `unavailable("not-ready")`
    (D-43-09);
  - **loopback validation (T-44-01-01)** — every ready component must have a `127.0.0.1` integer port
    in 1..65535; any violation → `unavailable("malformed")`, and the session is never served;
  - **deterministic expiry/rotation** — cache is bound to `startedAt` + `expiresAt` + live endpoint set;
    restart, targeted-restart endpoint drift and expiry all force a fresh session id;
  - **no secrets/env/paths** — the payload serializes only endpoints + timestamps + bounded flags.
- `desktop/src/main/index.ts` wires the provider into `getBootstrap` (runtime bootstrap as
  `DesktopBootstrap.runtime`), invalidates the cached session on `requestRuntimeRestart` and on
  `will-quit`, and lazily owns the provider alongside the runtime. `bridge-schema.ts` documents the
  new `getBootstrap` response shape.

### Frontend: single logical-service endpoint seam (`frontend/src/lib/runtime/endpoint-resolver.ts`)

- `RuntimeEndpointResolver.resolve()` returns `{ kind: "desktop"; sessionId; endpoints } | { kind:
  "browser"; endpoints } | { kind: "unavailable"; reason }`.
  - Desktop: `apiBaseUrl = http://<api host>:<port>/api` (absolute mirror of the rewrite route),
    `agentBaseUrl = http://<agent host>:<port>`.
  - Browser: `apiBaseUrl = "/api"`, `agentBaseUrl = ""` (existing relative routes → next rewrites).
  - Missing/stale/malformed bootstrap → typed unavailable; never a guessed URL.
- The bridge is consulted on every resolve (rotation detection) while the endpoint computation is
  cached per session id.
- Process-wide `endpointResolver` singleton shared by `client.ts` and `sse.ts`.

### Frontend transport wiring (`client.ts` + `sse.ts`)

- `client.ts`: an async request interceptor (registered before the token interceptor) sets
  `config.baseURL` from the resolver in desktop mode; browser mode and unavailable states keep the
  default base URL so existing request semantics are preserved. The token interceptor stays a
  synchronous handler (kept last), so the existing `api.test.ts` interceptor harness is unchanged.
- `sse.ts`: `streamAgentRun` awaits the resolver and prepends the desktop agent origin to relative
  `/agent/...` URLs; browser mode keeps the relative path (next rewrite). Streaming semantics
  (frames, aborts, terminals) are untouched.

## Verification (all executed)

1. `cd desktop && npm run typecheck` — exit 0, no errors.
2. `cd frontend && npx tsc --noEmit` — exit 0, no errors.
3. `cd desktop && npx playwright test --config tests/runtime/playwright.config.ts tests/runtime/bootstrap.test.ts` — **9 passed** (3 runs). New suite: ready-only gating, typed session + logical handles, payload redaction (no token/secret/env/path), deterministic expiry, restart rotation, degraded invalidation, malformed endpoint fail-closed, endpoint-drift invalidation, null-runtime availability.
4. `cd desktop && npx playwright test --config tests/runtime/playwright.config.ts` (full runtime suite) — **122 passed** (was 113; +9 bootstrap tests).
5. `cd frontend && npx vitest run src/lib/runtime/` — **8 passed**. New resolver suite: browser fallback, dynamic loopback bases, cache reuse, unavailable(not-ready/expired), malformed fail-closed, restart rotation, bridge-vanishing degrade.
6. `cd frontend && npx vitest run src/lib/api/ src/lib/sse.test.ts` — **7 passed** (SSE + api/ dir; plan's exact command).
7. `cd frontend && npx vitest run src/lib/api.test.ts src/lib/api.contract.test.ts` — **116 passed** (API client no regression, including interceptor order).
8. `cd frontend && npx vitest run src/lib/` (full lib) — **188 passed / 11 files**.
9. `cd frontend && npx vitest run src/components/desktop/ src/lib/desktop/capabilities.test.ts` — **31 passed** (RuntimeGate + RecoveryPanel + capabilities fixtures after the bridge shape change).
10. `cd frontend && npx vitest run src/components/analysis/agent-turn-inline.test.tsx src/components/analysis/agent-workspace-panel.test.tsx src/components/analysis/analysis-unified-chat.test.tsx src/components/reader/reader-chat-panel.test.tsx` — **45 passed** (SSE consumers).
11. `cd frontend && npm run build` — succeeded (standalone output). Bundle scan over `.next/static/`: **no** `127.0.0.1:8010` / `127.0.0.1:3100` / `127.0.0.1:8000`, **no** provider secret patterns; the resolver seam is present (expected — it is the runtime lookup), desktop contract modules are type-erased.
12. `git status --short` — see file list above; pre-existing user modifications (`.gitignore`, `.planning/config.json`, `backend/`, `deploy/`, `scripts/`, `frontend/next.config.mjs`, `frontend/next-env.d.ts`) untouched. `frontend/next.config.mjs` was **not** dirty at HEAD (verified), so no reconciliation was needed.

## Deviations from Plan

### Auto-fixed Issues (executor rules)

**1. [Rule 3 - Blocking / Rule 1 - Pre-existing bug] 43-04 `RuntimeRecoveryPanel.tsx` value-imports the shared runtime-status contract, breaking `next build`**
- **Found during:** Task 3 `npm run build` (the plan's own verification).
- **Issue:** `frontend/src/components/desktop/RuntimeRecoveryPanel.tsx` (committed at HEAD `1c075d6`,
  43-04, untouched by this plan) `import { isActionAllowed } from "…/desktop/src/shared/runtime-status"`
  pulls desktop runtime code into the client bundle; Turbopack fails with
  `Module not found: Can't resolve '../../../../desktop/src/shared/runtime-status'`. The build could
  not complete without fixing it.
- **Fix:** the shared contract module is now imported **type-only** (matching the established pattern
  in `frontend/src/lib/desktop/runtime-recovery.ts`), and the panel keeps its defense-in-depth
  allowlist check via a local `isActionAllowed` mirror implementing the exact same
  `recoveryActionIdsFor` matrix (T-43-04-02 behavior preserved; runtime allows and state actions
  unchanged).
- **Files modified:** `frontend/src/components/desktop/RuntimeRecoveryPanel.tsx`.
- **Verification:** full build green; RuntimeGate/RecoveryPanel component tests pass (20 tests).

**2. [Rule 3 - Blocking] Orphaned Electron standalone servers locked `.next/standalone`, blocking the build**
- **Found during:** Task 3 first `npm run build` (EBUSY rmdir on `.next/standalone`).
- **Issue:** three stale `electron.exe frontend/.next/standalone/server.js` processes (left over from a
  prior shell smoke run) held the standalone directory open.
- **Fix:** terminated exactly those three orphan PIDs (`taskkill //PID 44828/103384/25264 //F //T`); no
  files changed, no other processes touched.

**3. [Rule 1 - Bug] Bootstrap test killed-fault marker matched the wrong process**
- **Found during:** Task 1 first suite run (2 deterministic failures).
- **Issue:** the dev adapter spawns next through the `node` binary (`node <…>/next …`), so
  `record.command.includes("next")` matched nothing; the frozen-clock test also asserted a changed
  `issuedAt` without advancing the clock.
- **Fix:** use `ops.spawnedProcess("next")` (shared spawn-marker helper, same as the 43-01 state-machine
  suite) and advance the injected clock before the restart assertion.
- **Files modified:** `desktop/tests/runtime/bootstrap.test.ts` (test-only).

**4. [Rule 1 - Bug] Resolver cache test expected zero bridge round-trips**
- **Found during:** Task 2 first suite run (2 failures).
- **Issue:** the resolver must consult the bridge on every resolve to detect session rotation — caching
  endpoints without re-checking would dial a stale port after a restart (the exact bug the plan forbids).
  The "reuses cache" test asserted `bridge.calls() === 1` after two resolves; the rotation test rebuilt
  the session with new component ports but reused the old `services` handles.
- **Fix:** assert the endpoint computation is cached (2nd resolve returns identical endpoints) while the
  bridge is still consulted (calls 1 → 2); the rotation fixture now rebuilds `components` and
  `services` together.
- **Files modified:** `frontend/src/lib/runtime/__tests__/endpoint-resolver.test.ts` (test-only).

### Scope notes

- `desktop/src/main/index.ts` and `desktop/src/main/ipc/bridge-schema.ts` were modified (plan 44-01
  explicitly lists `desktop/src/runtime/bootstrap.ts` as a main-owned capability that is wired to the
  bridge; the plan's key-link is "typed DesktopBridge bootstrap capability", so the wiring points are in
  scope).
- The `DesktopBootstrap` contract gained the `runtime` field, so the bridge fixtures in
  `frontend/src/lib/desktop/capabilities.test.ts` and
  `frontend/src/components/desktop/__tests__/RuntimeGate.test.tsx` were updated (test-only; the plan's
  `files_modified` does not list them, but the plan's own acceptance criteria require the shared
  bootstrap shape change to be reflected in the frontend capability resolver tests).
- `frontend/src/components/desktop/RuntimeRecoveryPanel.tsx` is outside the plan's `files_modified` —
  it was a pre-existing build break that blocks the plan's verification (see deviation 1).

## Known Stubs

None. The runtime is `null` until ready by design (D-43-09); the resolver's `unavailable` states are the
intended typed behavior, not stubs.

## Threat Flags

None beyond the plan's register. The new renderer-facing surface is the `RuntimeBootstrap` payload and
the resolver: endpoints are loopback-allowlisted (T-44-01-01 mitigation), the payload schema is bounded
and secret-free (T-44-01-02 mitigation), and stale/expired/malformed bootstraps resolve to typed
unavailable states instead of guessed URLs. No new network/file/process surface was added.

## Self-Check: PASSED

- 5 files created, 9 files modified — all under `desktop/src/`, `frontend/src/` and
  `.planning/phases/44-desktop-transport-credentials-and-offline-behavior/` (verified by `git status
  --short`).
- `npm run typecheck` (desktop) clean, `npx tsc --noEmit` (frontend) clean; bootstrap suite 9/9,
  resolver suite 8/8, API/SSE regression 123/123 (api 116 + sse 7), full lib 188/188, desktop runtime
  suite 122/122; `next build` green; client bundle scan free of fixed ports and secrets.
- No commits created (orchestrator-owned); no user-modified files were touched or overwritten
  (`frontend/next.config.mjs` was verified clean at HEAD and left untouched; a build-generated
  `frontend/next-env.d.ts` change was restored to its pre-build state).
