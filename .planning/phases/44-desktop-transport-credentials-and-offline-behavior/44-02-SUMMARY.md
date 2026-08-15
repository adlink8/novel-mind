---
phase: 44-desktop-transport-credentials-and-offline-behavior
plan: "02"
subsystem: os-protected-credentials-and-local-session-auth
tags: [desktop, credentials, safeStorage, dpapi, local-auth, audience-bound, fail-closed, session-token]
requires:
  - "44-01 (one-session runtime bootstrap contract + session id rotation)"
  - "43-03 (app-data layout with secrets root under %APPDATA%/NovelMind)"
  - "42-01/42-03 (secure bridge: renderer never receives secrets)"
provides:
  - "Main-owned OS-protected credential store (credential-store.ts) — safeStorage/DPAPI encrypted blobs under the app-data secrets root with honest unavailable/decrypt-failed/rotation-needed states and a redacted status contract"
  - "Main-owned audience/expiry-bound local session token minting (local-auth.ts) — separate tokens for backend vs Agent Service, session-bound (sid), HMAC secret rotates on restart"
  - "Backend DesktopLocalAuthMiddleware (desktop_local_auth.py) — iss/aud/exp/iat/sid/jti + loopback-source validation, fail-closed 401, unconfigured pass-through keeps browser JWT/cookie auth"
  - "Agent Service desktop-local-auth middleware (desktop-local-auth.ts) — verifyLocalSessionToken/requireLocalSession with constant-time signature comparison, loopback-source gate"
  - "Dedicated unit suites for both middlewares and the credential store"
affects:
  - "44-03 (carries session/auth bootstrap into SSE transport; wires the agent-service guard into server.ts and the renderer auth bridge)"
  - "45-01 (packaged adapter injects NOVELMIND_LOCAL_AUTH_SECRET / minted tokens into owned process environments)"
tech-stack:
  added:
    - "No new dependencies — Electron safeStorage (async API), PyJWT (already present), node:crypto"
  patterns:
    - "Pure shared status contract (credential-status.ts) crossing main→renderer like bootstrap-contract.ts — booleans/stable states only, never values"
    - "Electron-free modules (credential-store.ts, local-auth.ts) with injected safeStorage/clock seams for deterministic unit tests"
    - "Injected verification seams on both services (secret + clock) so the guards are testable without real OS/processes"
    - "Audience separation: backend `novelmind-desktop-local` vs agent `novelmind-agent-local`; constant-time signature comparison; loopback-source gate"
key-files:
  created:
    - desktop/src/shared/credential-status.ts (RedactedCredentialStatus + CredentialState)
    - desktop/src/security/credential-store.ts (CredentialStore + SafeStorage seam + format v1 blobs)
    - desktop/src/security/local-auth.ts (DesktopLocalAuth + audience constants)
    - desktop/tests/security/credential-store.test.ts (16 tests: store + local-auth + namespace contract)
    - desktop/tests/security/playwright.config.ts (pure-Node unit config mirroring tests/runtime)
    - backend/app/middleware/desktop_local_auth.py (DesktopLocalAuthMiddleware + verifier)
    - backend/app/middleware/__init__.py
    - backend/tests/security/test_desktop_local_auth.py (11 tests)
    - agent-service/src/middleware/desktop-local-auth.ts (verifyLocalSessionToken / requireLocalSession / extractEndUserToken)
    - agent-service/tests/desktop-local-auth.test.ts (17 tests)
  modified:
    - backend/app/config.py (local_auth_secret bound to NOVELMIND_LOCAL_AUTH_SECRET)
    - backend/app/main.py (mount DesktopLocalAuthMiddleware with settings.local_auth_secret — pass-through when unconfigured)
decisions:
  - "safeStorage guarantee described accurately as Windows user-bound DPAPI protection — never an absolute boundary (44-RESEARCH)."
  - "Blob format v1 stores keyId + base64 encrypted payload; rotation is detected by keyId mismatch OR decrypt failure and healed by re-encrypting with the current key; a value the current key cannot decrypt fails closed (rotation_needed, never plaintext fallback)."
  - "Backend middleware is OPT-IN: unconfigured (browser dev) passes through to the existing JWT/cookie auth; configured (desktop, main injects NOVELMIND_LOCAL_AUTH_SECRET) enforces audience/expiry/loopback — explicit config, never implicit bypass (D-44-04)."
  - "Agent Service guard is fail-closed on missing secret by construction (no dev-bypass path); the desktop adapter injects a real secret when the service is launched. Transport wiring into server.ts lands in 44-03 per the plan file list."
  - "Separate short-lived (5 min) tokens per service with distinct audiences; HMAC secret rotates per runtime session so prior-session tokens are rejected (T-44-02-03)."
metrics:
  duration_minutes: 110
  completed_at: "2026-08-10"
---

# Phase 44 Plan 02: OS-Protected Credentials and Fail-Closed Local Session Auth — Summary

Moved credentials out of renderer reach with a main-owned OS-protected credential store
(safeStorage/DPAPI encrypted blobs, redacted status only) and established audience- and
expiry-bound fail-closed local session authentication for the local FastAPI backend and the
Agent Service (separate audiences, loopback-source gate, restart secret rotation).

## What Was Built

### Task 1 — OS-protected credential storage and redacted status

- `CredentialStore` (Electron-free, injected `SafeStorage` seam): encrypts NOW with the
  current safeStorage key and persists only the encrypted envelope under the app-data
  `secrets/` root (path containment via `containPath`). Never a plaintext fallback, never
  renderer `localStorage`, never a secret-bearing log (T-44-02-01).
- Honest states distinguish `unavailable` / `decrypt_failed` (malformed blob) /
  `rotation_needed` (OS key changed) / `available`; `rotate()` re-encrypts every blob and
  fails closed on any unreadable blob.
- The only renderer-visible surface is `RedactedCredentialStatus` (provider + localAuth
  state strings + storageAvailable) — no value, key, or blob fragment ever leaves main.
- `DesktopLocalAuth` mints separate short-lived (5 min) HS256 tokens for backend
  (`novelmind-desktop-local`) and agent (`novelmind-agent-local`), session-bound via `sid`,
  with a per-instance HMAC secret that rotates on restart (T-44-02-03).

### Task 2 — audience- and expiry-bound local service authentication

- Backend `DesktopLocalAuthMiddleware`: validates iss/aud/exp/iat/sid/jti + loopback
  source; missing/invalid/expired/wrong-audience/non-loopback → 401 (fail closed). OPT-IN:
  unconfigured passes through so browser dev (existing JWT/cookie) is preserved — explicit
  config only, never implicit bypass (D-44-04).
- Agent Service `desktop-local-auth.ts`: `verifyLocalSessionToken` (constant-time HMAC
  comparison, leeway-bounded exp/iat) + `requireLocalSession` (loopback-source gate,
  missing-secret fail-closed 401) + `extractEndUserToken` for forwarding the end-user JWT
  (owner isolation stays on FastAPI).
- `backend/app/config.py` gained `local_auth_secret` bound to `NOVELMIND_LOCAL_AUTH_SECRET`
  (verified end-to-end env binding).
- `backend/app/main.py` mounts the middleware with `settings.local_auth_secret`.

### Task 3 — Test, Fix, and Confirm

- All three suites ran twice (see numbers), corrupt-storage and stale/rotated-token paths
  fail closed, and the secret scan is empty.

## Verification

- Desktop security suite (16 tests): **passed twice** — roundtrip encrypted-blob-only,
  redaction (status never contains the value), unavailable fails closed with zero writes,
  corrupt blob → decrypt_failed, OS key rotation → rotation_needed → rotate() heals,
  non-retained rotation → fail closed, write denial → write_failed, delete, invalid
  namespace/key, local-auth audience separation / null-session / rotate invalidation /
  expiry bounds / token-never-in-status.
- Backend `tests/security/test_desktop_local_auth.py` (11 tests): **passed twice** —
  valid succeeds; missing / non-Bearer / wrong audience / expired / wrong secret / tampered
  / old-restart-secret / external-source all 401; unconfigured pass-through; helpers.
- Backend `tests/unit/api/test_auth.py`: **27 passed** (auth no regression).
- Backend `tests/security/` full dir: **51 passed** (incl. production baseline).
- Agent Service `tests/desktop-local-auth.test.ts` (17 tests): **passed twice**.
- Agent Service full suite: **28 files / 1056 passed**.
- `desktop npm run typecheck` / `agent-service npx tsc --noEmit` / `frontend npx tsc --noEmit`: clean.
- `frontend npx vitest run src/lib/`: **188 passed / 11 files** (matches 44-01 baseline).
- Secret scan: no plaintext fixture secret appears anywhere outside the test source file;
  no `Bearer` token literals in desktop security source; no token/env value in the status
  surface, renderer payload, or logs.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `_reject()` returned a Response but was raised (TypeError in middleware)**
- **Found during:** Task 2, first backend run — `raise _reject()` → `TypeError: exceptions must derive from BaseException`.
- **Fix:** return the 401 JSONResponse from `dispatch` instead of raising.
- **Files modified:** `backend/app/middleware/desktop_local_auth.py`.

**2. [Rule 1 - Bug] `DesktopLocalAuth` field/method name collision (`secret`)**
- **Found during:** desktop `npm run typecheck` — `TS2300: Duplicate identifier`.
- **Fix:** renamed the private field to `currentSecret`; public `secret()` accessor unchanged.
- **Files modified:** `desktop/src/security/local-auth.ts`.

**3. [Rule 1 - Bug] Rotation model was unsound for a non-retained OS key**
- **Found during:** Task 1 suite — `rotate()` claimed to heal a value the current key could
  not decrypt.
- **Fix:** blobs store `keyId`; rotation is detected by keyId mismatch (retained key) OR
  decrypt failure; `rotate()` only re-encrypts decryptable values and fails closed
  (`decrypt_failed`) on anything it cannot open.
- **Files modified:** `desktop/src/security/credential-store.ts` + tests.

### Rule 2 Additions (critical functionality for the plan's goal)

**4. [Rule 2 - Auth] Backend middleware mounted in `app/main.py`**
- The plan's file list omits `main.py`, but "Require audience- and expiry-bound local
  service authentication" is only real once the middleware is mounted. Wiring is zero-risk:
  unconfigured (browser dev) passes through unchanged. Documented so the verifier sees it.
- **Files modified:** `backend/app/main.py`.

**5. [Rule 3 - Blocking] `desktop/tests/security/playwright.config.ts` test runner**
- The plan's verify runs `npm test -- --run tests/security/credential-store.test.ts`, but
  the existing `tests/runtime/playwright.config.ts` scopes `testDir` to `tests/runtime`, so
  the new suite could not be collected. Added a mirror pure-Node config (same pattern as the
  runtime config). All tests run in-process with no Electron/renderer.
- **Files modified:** `desktop/tests/security/playwright.config.ts` (new).

### Scope Notes (deliberate, per plan file list)

- **Renderer JWT / client.ts unchanged.** The plan's `files_modified` for this wave does not
  include `frontend/src/lib/api/client.ts`; browser mode intentionally preserves the
  `sessionStorage` JWT + Bearer flow, and the desktop renderer→local-service session-token
  bridging is the 44-03 transport wiring ("Carry session/auth bootstrap into existing SSE
  connection"). Desktop mode already has NO renderer-held secret from this plan: the
  credential store and token minting are main-owned, and services only accept the
  audience-bound session tokens.
- **agent-service server.ts / config.ts not wired.** The plan scopes agent-service to the
  middleware module + its test file only; wiring `requireLocalSession` into the HTTP server
  (and token attachment on the poller's backend calls) is explicitly 44-03's transport work.
  Reverted a preliminary server.ts edit to stay in-scope; the module is fully unit-tested
  and ready to wire.
- **Credential/local-auth not yet consumed by bootstrap or adapters** (secret injection into
  owned process envs). The `DesktopLocalAuth.secret()` accessor is the injection source;
  adapter/bootstrap wiring lands with the 44-03 transport work and 45-01 packaged adapter.

## Known Stubs

None. The middleware modules and store are fully implemented and tested; transport wiring
is the explicit deliverable of plan 44-03, not a stub.

## Threat Flags

No new security-relevant surface beyond the plan's `<threat_model>`: the added endpoints
are validation-only guards on the existing loopback HTTP surface, covered by T-44-02-01/02/03.

## Self-Check

- [x] `desktop/tests/security/credential-store.test.ts` exists and passes (16 tests, twice).
- [x] `backend/tests/security/test_desktop_local_auth.py` exists and passes (11 tests, twice).
- [x] `agent-service/tests/desktop-local-auth.test.ts` exists and passes (17 tests, twice).
- [x] Secret scan empty; user dirty files untouched; no stray untracked artifacts.
- [x] `git status` shows only plan files + documented additions (plus pre-existing user changes).

## Self-Check: PASSED
