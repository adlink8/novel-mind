---
phase: 44-desktop-transport-credentials-and-offline-behavior
plan: "03"
subsystem: sse-transport-credentials-and-offline-behavior
tags: [desktop, sse, reconnect, terminal, cancellation, replay, local-auth, bridge, offline, capability-gate, honest-blocking]
requires:
  - "44-01 (one-session runtime bootstrap + renderer endpoint resolver)"
  - "44-02 (OS-protected credential store + audience-bound DesktopLocalAuth + service-side guards)"
  - "43-04 (RuntimeGate renderer wiring)"
provides:
  - "Renderer→main bridge capability `getLocalAuthToken` (short-lived audience-bound session token; HMAC secret stays main-owned)"
  - "DevelopmentProcessAdapter injects NOVELMIND_LOCAL_AUTH_SECRET into the owned agent-service env; agent-service enforces fail-closed local-session auth on every inbound run"
  - "SSE `runAgentStream` driver: authoritative completed|cancelled|failed terminals, single reconnect after mid-stream drop, event-id replay dedupe, session-rotation invalidation, never-synthesize-success"
  - "Per-capability offline/provider states (capability-status.ts) + ProviderCapabilityGate: local reader/editor/library/data stay available offline; provider generation/embedding/image are honestly blocked/unavailable/misconfigured"
  - "Desktop integration suites (sse-recovery + offline-workflows) proving terminals and offline matrix over real sockets"
affects:
  - "45-01 (packaged adapter injects NOVELMIND_LOCAL_AUTH_SECRET into bundled runtimes)"
tech-stack:
  added:
    - "No new dependencies — node:http mock servers in Playwright integration suites, existing Electron safeStorage/bridge"
  patterns:
    - "Header split: local session token travels in `X-Local-Auth-Token`; end-user JWT stays in `Authorization` (agent-service extracts/forwards the latter for FastAPI owner checks)"
    - "Pure renderer capability derivation from redacted signals (runtime readiness + credential state + reachability probe + last request) — never one global online flag"
key-files:
  created:
    - desktop/src/runtime/bootstrap.ts (currentSessionId() added)
    - desktop/tests/integration/sse-recovery.spec.ts (5 tests)
    - desktop/tests/integration/offline-workflows.spec.ts (3 tests)
    - desktop/tests/integration/playwright.config.ts (pure-Node integration runner)
    - frontend/src/lib/runtime/capability-status.ts (typed per-capability status)
    - frontend/src/lib/runtime/__tests__/capability-status.test.ts (8 tests)
    - frontend/src/components/desktop/ProviderCapabilityGate.tsx (honest provider gate)
    - agent-service/tests/server-local-auth.test.ts (3 gating tests)
  modified:
    - desktop/src/shared/bridge-contract.ts (getLocalAuthToken + credentials on bootstrap)
    - desktop/src/preload/index.ts (channel + capability)
    - desktop/src/main/ipc/bridge-schema.ts (channel schema)
    - desktop/src/main/index.ts (localAuth + credentialStore wiring, token handler, redacted credential status)
    - desktop/src/runtime/development-process-adapter.ts (localAuthSecret injection)
    - desktop/tests/shell-smoke.spec.ts / desktop/tests/e2e/renderer-privileges.spec.ts / desktop/tests/security/ipc.spec.ts (6-capability surface, credentials field)
    - frontend/src/lib/desktop/capabilities.ts (+ capabilities.test.ts) (getLocalAuthToken resolver)
    - frontend/src/lib/runtime/__tests__/endpoint-resolver.test.ts (fixture credentials)
    - frontend/src/components/desktop/__tests__/RuntimeGate.test.tsx (fixture credentials)
    - frontend/src/lib/sse.ts (session token bridge + runAgentStream)
    - frontend/src/lib/sse.test.ts (16 tests incl. 9 new runAgentStream semantics)
    - agent-service/src/config.ts (localAuthSecret)
    - agent-service/src/middleware/desktop-local-auth.ts (buildLocalAuthHeader + IPv4-mapped loopback)
    - agent-service/src/server.ts (verifyInboundSession gate)
    - agent-service/tests/desktop-local-auth.test.ts (buildLocalAuthHeader tests)
    - backend/app/middleware/desktop_local_auth.py (IPv4-mapped loopback acceptance)
decisions:
  - "Local session token travels in a dedicated `X-Local-Auth-Token` header (renderer→agent transport), NOT the Authorization header: Authorization keeps the end-user JWT for FastAPI owner isolation. The agent service verifies the session token first (fail closed), then forwards the end-user JWT unchanged."
  - "Backend local-auth middleware stays OPT-IN in this wave: injecting NOVELMIND_LOCAL_AUTH_SECRET into the FastAPI env would 401 the renderer's user-JWT API calls and the readiness probe until the endpoint-auth story is completed (45-01). The agent-service guard is fully wired because it authenticates the SSE run surface only, which the renderer bridges with the session token."
  - "SSE reconnect is bounded to ONE retry and only after a frame has been received: a drop before any frame, a non-2xx, or a drop after a terminal frame never reconnects and never synthesizes success. A silent stream end without a terminal resolves as `failed`."
  - "Event replay dedupe keys on `event_id` (string|number) emitted in the frame; no event_id means no dedupe (existing behavior). The run-start id is passed as `eventId` for context."
  - "Per-capability offline state derives from three redacted signals; `navigator.onLine` is only a coarse probe, never universal internet truth; a failed provider request keeps the capability blocked."
metrics:
  duration_minutes: 240
  completed_at: "2026-08-10"
---

# Phase 44 Plan 03: SSE Terminal Semantics and Per-Capability Offline Behavior — Summary

Wired the 44-02 local session auth into the renderer transport and the managed agent
service, preserved authoritative SSE terminals across reconnect/cancellation/replay/rotation
(never converting a timeout/disconnect into success), and proved offline honesty: local
reader/editor/library/data workflows stay available while provider generation/embedding/image
actions are blocked with explicit reasons and zero fabricated artifacts.

## What Was Built

### Task 1 — Renderer session-token bridge + agent-service fail-closed wiring + SSE semantics

- **Bridge capability `getLocalAuthToken(target)`** (main→renderer): mints a short-lived
  (5 min) audience-bound session token for `agent` (and `backend`) bound to the current
  bootstrap session id. Null when no runtime session exists — fail closed. The HMAC secret,
  provider keys and credential store stay main-owned; the renderer holds only this expiring
  session token (D-44-02/D-44-03).
- **Bootstrap payload now carries `credentials`** — the redacted provider/local-auth state
  (state strings only, never a value), from the new `CredentialStoreInstance()` wired into
  main via async Electron `safeStorage`.
- **DevelopmentProcessAdapter injects `NOVELMIND_LOCAL_AUTH_SECRET`** into the owned
  agent-service env at spawn (read at spawn time; rotates on restart). The agent service now
  enforces local-session auth on every inbound run request: session token required first
  (loopback source + HS256 + audience/expiry, fail closed), end-user JWT forwarded for FastAPI
  owner checks.
- **SSE `runAgentStream`** (new driver over `streamAgentRun`): authoritative
  `completed|cancelled|failed` terminals, single reconnect only after a frame was received,
  event-id dedupe (no double materialization), session-rotation rejection (`code:
  session-rotated` forces clean re-bootstrap), AbortError on cancellation, and a silent stream
  end without a terminal resolves as `failed` — never success.

### Task 2 — Per-capability offline/provider states

- `capability-status.ts` derives typed per-capability availability from the redacted provider
  credential state + reachability probe + last request result — never one global online flag
  (T-44-03-03). Local capabilities are always `available` when the runtime is ready (D-44-06).
- `ProviderCapabilityGate` renders provider operations honestly:
  available / blocked / unavailable / misconfigured with explicit reasons; never executes,
  never returns empty-success, never fabricates an artifact (D-44-07).

### Task 3 — Test, Fix, and Confirm

All suites ran twice (see Verification). One real bug found and fixed during the desktop
integration run (Rule 1, IPv4-mapped loopback).

## Verification

- `desktop npm run typecheck`: **clean** (twice; includes the new integration specs).
- `frontend npx tsc --noEmit`: **clean**.
- `frontend npx vitest run src/lib/sse.test.ts src/lib/runtime/`: **32 passed** (16 sse +
  8 endpoint-resolver + 8 capability-status) — twice.
- `frontend npx vitest run` full: **803 passed / 79 files**.
- `backend venv/Scripts/python.exe -m pytest tests/security/ -q`: **51 passed** — twice
  (incl. the 11 desktop-local-auth tests; loopback IPv4-mapped fix regression-covered).
- `backend ... tests/unit/api/test_auth.py`: **27 passed** (auth no regression).
- `agent-service npx vitest run` full: **1062 passed / 29 files** (incl. new
  `server-local-auth.test.ts` 3 gating tests + `desktop-local-auth.test.ts` 20) — twice.
- `agent-service npx tsc --noEmit`: clean.
- `desktop npx playwright test --config tests/integration/playwright.config.ts`:
  **8 passed** (5 sse-recovery + 3 offline-workflows) — twice.
- Plan's exact desktop command `npx playwright test tests/integration/sse-recovery.spec.ts
  tests/integration/offline-workflows.spec.ts` (root Electron harness): **8 passed**.
- `desktop npx playwright test` full root-config: **42 passed, 2 failed** — the 2 failures
  (`critical-workflows` login hydration, `route-parity` static assets) are PRE-EXISTING
  environmental: the local `.next/standalone` tree lacks `public/` and `.next/static` (a
  `desktop/proof/scripts/build-next-standalone.ps1` output), so the smoke renderer serves
  JS/CSS as `text/plain` and React never hydrates. Verified unrelated to this plan's changes
  (debug probe confirmed the bridge boots fine: `bootstrap-ok` with the new `credentials`
  field). Logged as a deferred item.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] IPv4-mapped IPv6 loopback rejected by the local-auth guard**
- **Found during:** agent-service `server-local-auth.test.ts` first run — `listen(0)` binds a
  dual-stack socket, so a loopback IPv4 connection presents as `::ffff:127.0.0.1`, which the
  middleware's loopback set rejected (401 on a valid token).
- **Fix:** added `::ffff:127.0.0.1` to the loopback sets in both the agent-service
  `requireLocalSession` guard and the backend `desktop_local_auth.py` middleware.
- **Files modified:** `agent-service/src/middleware/desktop-local-auth.ts`,
  `backend/app/middleware/desktop_local_auth.py`.

**2. [Rule 1 - Bug] `dispatchFrame` swallowed onEvent exceptions (misleading integration test)**
- **Found during:** desktop integration suite — assertions thrown inside `onEvent` were routed
  to the SSE malformed-frame handler instead of failing the test.
- **Fix:** integration tests collect frames and assert after the stream resolves (no
  assertion inside the callback). No production code change.
- **Files modified:** `desktop/tests/integration/sse-recovery.spec.ts`.

### Scope Notes (deliberate, per plan intent)

- **`desktop/src/runtime/bootstrap.ts` gained `currentSessionId()`** — needed so main can bind
  the minted session token to the exact bootstrap session the renderer dials (44-02's
  `DesktopLocalAuth` needs the live session id; `tokens()` fails closed when null).
- **Backend FastAPI local-auth remains OPT-IN (unconfigured pass-through).** Injecting the
  secret into the FastAPI env would 401 the renderer's user-JWT API calls and the loopback
  readiness probe. The backend middleware contract, unit suite and config binding were
  delivered in 44-02; flipping the backend to mandatory desktop auth is tracked for the 45-01
  packaged-adapter wave (endpoint-level user-JWT + local-session coexistence).
- **Renderer `client.ts` untouched (sessionStorage JWT kept)** — the browser dev path stays
  byte-identical; desktop SSE authentication is carried by the new `X-Local-Auth-Token`
  header instead. `sse.ts` imports were refactored from the `@/lib/api` barrel to the direct
  `./api/client` module so the desktop pure-Node integration specs can import it without the
  barrel's Next-only exports.
- **`endpoint-resolver` cache test count** changed only by the added fixture fields
  (`credentials`) on the mock bridge — resolver behavior unchanged.

## Known Stubs

None. The `runAgentStream` driver is fully implemented and unit/integration tested. The
desktop offline matrix is proven over real sockets; provider-credential UI wiring (rendering
the gate into the generation/embedding/image surfaces) is an application-level integration
that the plan's `key_links` (capability-status → ProviderCapabilityGate) delivers as a
reusable component + typed status.

## Threat Flags

- `X-Local-Auth-Token` is a new renderer→agent HTTP header carrying the short-lived session
  token. Covered by T-44-02-02 (audience/expiry/loopback fail-closed guard on the agent
  service) and T-44-03-01/02 (replay dedupe, rotation rejection, no-success-on-timeout). The
  header is only meaningful on the loopback agent endpoint; a LAN request is 401 even with a
  valid token.
- No other new network/auth/file surface beyond the plan's `<threat_model>`.

## Self-Check

- [x] `desktop/tests/integration/sse-recovery.spec.ts` exists and passes (5 tests, twice).
- [x] `desktop/tests/integration/offline-workflows.spec.ts` exists and passes (3 tests, twice).
- [x] `frontend/src/lib/runtime/__tests__/capability-status.test.ts` exists and passes (8).
- [x] `agent-service/tests/server-local-auth.test.ts` exists and passes (3).
- [x] All required suites pass twice; frontend/desktop/agent typechecks clean.
- [x] `git status` shows only plan files + documented additions (plus pre-existing user changes).
- [x] No plaintext secrets in any created file; token values never logged.

## Self-Check: PASSED
