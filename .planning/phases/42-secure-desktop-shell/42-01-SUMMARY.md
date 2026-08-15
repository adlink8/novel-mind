---
phase: 42-secure-desktop-shell
plan: 01
subsystem: desktop-shell
tags: [electron, typescript, contextbridge, sandbox, playwright, security]

# Dependency graph
requires:
  - phase: 41-electron-architecture-and-packaging-proof
    provides: Next standalone renderer artifact (frontend/.next/standalone), Electron 43.3.0 embedded-Node prerequisite proof, 13-route parity proof, approved dependency set
provides:
  - Production Electron main/preload/shared boundary under desktop/
  - Typed DesktopBridge contract (4 capabilities) shared across preload and renderer
  - Secure BrowserWindow factory (sandbox, contextIsolation, no nodeIntegration, webSecurity, nav/window/download policy)
  - Playwright shell smoke suite with privilege-negative assertions incl. browser-mode fallback
affects: [42-02, 42-03, 42-04, 42-05]

# Tech tracking
tech-stack:
  added: [electron ^43.3.0, electron-builder ^26.15.3, @playwright/test 1.61.1, typescript ^5.9.3 (desktop package)]
  patterns:
    - "capability-specific preload bridge via contextBridge (no generic IPC)"
    - "main-side window security posture registry (no runtime API probing)"
    - "loopback-origin allowlist for window creation, navigation and IPC sender validation"
    - "privilege-negative smoke tests + browser-mode fallback test"

key-files:
  created:
    - desktop/package.json
    - desktop/tsconfig.json
    - desktop/tsconfig.build.json
    - desktop/src/main/index.ts
    - desktop/src/main/create-window.ts
    - desktop/src/preload/index.ts
    - desktop/src/shared/bridge-contract.ts
    - desktop/tests/shell-smoke.spec.ts
    - desktop/tests/global-setup.ts
    - desktop/tests/global-teardown.ts
    - desktop/tests/smoke-server.ts
    - desktop/playwright.config.ts

key-decisions:
  - "Phase 41-03 NO-GO verdict preserved verbatim; 42-45 execution authorized via config.json gate_overrides.phase_42_45_execution with prerequisite #1 evidence (desktop/proof/bundled-node-evidence.json)"
  - "Dependency set frozen from Phase 41-01 approved versions: electron ^43.3.0, electron-builder ^26.15.3 (no install/approval repeat)"
  - "Next renderer stays web-compatible: no electron imports, no window.novelMindDesktop required in browser mode"
  - "Sandboxed preload inlines IPC channel strings; contract constants are the shared source of truth, drift caught end-to-end by smoke suite"
  - "Window security flags tracked via main-side posture registry because Electron 43 typings omit getLastWebPreferences"

patterns-established:
  - "one bridge method per capability, one IPC channel per method, sender+frame-origin validated in every handler"
  - "globalSetup compiles desktop TS and starts the existing Next standalone via ELECTRON_RUN_AS_NODE on a dynamic loopback port"
  - "isApprovedAppUrl loopback allowlist gates loadURL, will-navigate, will-redirect and IPC sender frames"

requirements-completed: [REQ-DESK-01, REQ-DESK-02]

# Metrics
duration: 75min
completed: 2026-08-10
---

# Phase 42 Plan 01: Secure Desktop Shell Summary

**Electron 43.3.0 production main/preload boundary with a typed 4-capability DesktopBridge, sandboxed+isolated BrowserWindow hosting the existing Next renderer, and a 8-test privilege-negative Playwright smoke suite (incl. browser-mode fallback) — verified green on Windows.**

## Task 1 (blocking decision) — Authorization Record

- `41-DECISION.md` Verdict is **NO-GO** and was **NOT modified** (honest record preserved).
- User-authorized override `gate_overrides.phase_42_45_execution` in `.planning/config.json`
  (authorized_at 2026-08-10, scope `execution_phase_42-01_to_45-04`) authorizes 42-45 execution
  despite the NO-GO; `preserve_phase_41_verdict: true`.
- Phase 41 prerequisite #1 (bundled Node via `ELECTRON_RUN_AS_NODE=1`) is **proven** in
  `desktop/proof/bundled-node-evidence.json` (Electron 43.3.0 embedded Node v24.18.1; root + dynamic
  routes 200; 15/15 static assets; owned shutdown).
- Dependency set carried from Phase 41-01 approval: **electron ^43.3.0**, **electron-builder
  ^26.15.3** (installed; no re-approval).

## Performance

- **Duration:** ~75 min
- **Completed:** 2026-08-10
- **Tasks:** 3
- **Files modified:** 12 created (839 lines) under `desktop/` only

## Accomplishments

- Production-shaped Electron package under `desktop/` (isolated from `desktop/proof/`), hosting the
  existing Next standalone renderer without touching a single frontend source file.
- BrowserWindow hardened by construction: `contextIsolation: true`, `sandbox: true`,
  `nodeIntegration: false`, `webSecurity: true`, `allowRunningInsecureContent: false`, permissions
  denied, popups/new-windows denied, `<webview>` blocked, downloads blocked, navigation/redirect
  restricted to the loopback origin allowlist.
- DesktopBridge exposes exactly four capabilities — `getRuntimeStatus`, `requestRuntimeRestart`,
  `getBootstrap`, `onRuntimeStatus` — no generic send/invoke/on, no filesystem/shell/env/process.
  Every IPC handler validates sender webContents + frame origin before answering (D-42-05).
- 8/8 shell smoke tests pass, including negative assertions (no `require`/`process`/`module`/
  `ipcRenderer`, blocked popup + external nav, minimal bootstrap payload) and a browser-mode check
  proving the renderer renders without `window.novelMindDesktop`.
- `frontend` typecheck (`tsc --noEmit`) exit 0; zero electron imports in `frontend/src`.

## Task Commits

No commits created by this executor — the orchestrator commits atomically (per the explicit
execution brief).

## Files Created/Modified

| File | Lines | Purpose |
|---|---|---|
| `desktop/package.json` | 23 | Production desktop package; electron ^43.3.0, electron-builder ^26.15.3, playwright 1.61.1 |
| `desktop/tsconfig.json` | 22 | Strict TS, CJS, `esModuleInterop`, DOM lib for tests |
| `desktop/tsconfig.build.json` | 9 | Emit config for tsc build (src → dist) |
| `desktop/.gitignore` | 4 | node_modules, dist, test-results, playwright-report |
| `desktop/src/shared/bridge-contract.ts` | 89 | Pure typed DesktopBridge contract + IPC channel constants (shared by preload/main; type-only for renderer) |
| `desktop/src/preload/index.ts` | 62 | Sandboxed preload; contextBridge exposes exactly the 4 bridge methods |
| `desktop/src/main/create-window.ts` | 117 | Secure BrowserWindow factory; loopback allowlist; nav/window/download/permission policy; posture registry |
| `desktop/src/main/index.ts` | 136 | App lifecycle, renderer URL validation, trusted-sender IPC handlers, restart capability |
| `desktop/tests/shell-smoke.spec.ts` | 218 | 8-test privilege-negative suite (desktop + browser mode) |
| `desktop/tests/global-setup.ts` | 97 | Compiles desktop, starts Next standalone via Electron-embedded Node on dynamic port |
| `desktop/tests/global-teardown.ts` | 25 | taskkill /T /F owned shutdown of the standalone renderer |
| `desktop/tests/smoke-server.ts` | 12 | Process-wide handle sharing between setup/teardown |
| `desktop/playwright.config.ts` | 29 | Serial playwright config with global setup/teardown |

## Decisions Made

- **Bridge capability set:** exactly the four plan-mandated methods; bootstrap payload carries no
  secrets/env/absolute paths (T-42-01-02).
- **Channel constants:** sandboxed preload inlines channel strings (it cannot require local modules);
  `DESKTOP_IPC_CHANNELS` in the shared contract is the source of truth and the smoke suite catches
  drift end-to-end (test 2 asserts exact exposed keys; bridge round-trips prove channel wiring).
- **Security posture registry:** Electron 43 typings do not expose `getLastWebPreferences`; the main
  process records each window's posture at creation and the bridge reports it (asserted equal to the
  baseline flags by the smoke suite).
- **Renderer URL:** injected via `NOVELMIND_RENDERER_URL` (validated against the loopback allowlist);
  dev default `http://127.0.0.1:3000`. Phase 43 replaces env injection with the service orchestrator.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Desktop tsconfig gaps broke `npm run typecheck`**
- **Found during:** Task 2/3 verification
- **Issue:** `esModuleInterop` missing (node:path default import), DOM lib missing (test `window`/`document`), and Electron 43 typings lack `getLastWebPreferences`.
- **Fix:** added `esModuleInterop` + `"DOM"` lib; introduced main-side security posture registry in `create-window.ts` (`SECURE_POSTURE` + `securityPostureFor`), read by `index.ts` status; spec asserts main-side URL + bridge-reported posture instead of the untyped runtime API.
- **Files modified:** `desktop/tsconfig.json`, `desktop/src/main/create-window.ts`, `desktop/src/main/index.ts`, `desktop/tests/shell-smoke.spec.ts`

**2. [Rule 3 - Blocking] Electron binary absent after `npm install`**
- **Found during:** Task 2 verification (install)
- **Issue:** `npm install` reported success but electron's postinstall never produced `node_modules/electron/dist/` (no `path.txt`); the install.js re-run hung on the network download despite a fully-cached artifact (144 MB `electron-v43.3.0-win32-x64.zip`, same hash/version as the proof package).
- **Fix:** extracted the cached zip to `desktop/node_modules/electron/dist` and wrote `path.txt` (exactly what electron's install.js does); `electron.exe` verified (43.3.0). No network, no package substitution.
- **Files modified:** none (local node_modules artifact only)

**3. [Rule 1 - Bug] Main-side flag assertion used `require` inside Playwright's serialized eval**
- **Found during:** Task 3 (smoke run)
- **Issue:** `electronApp.evaluate` cannot see `require`; first smoke run failed 1/8.
- **Fix:** assert the main window loads the approved loopback URL on the main side and assert all four flags via the bridge-reported posture (which the main process computes from the registry over real IPC).
- **Files modified:** `desktop/tests/shell-smoke.spec.ts`

**4. [Rule 3 - Blocking] `@playwright/test` caret range resolved to an uninstalled browser revision**
- **Found during:** Task 2 scaffolding
- **Issue:** `^1.61.1` could install a newer Playwright whose chromium revision is not in the local `%LOCALAPPDATA%\ms-playwright` cache.
- **Fix:** pinned `@playwright/test` to exact `1.61.1` (matches frontend/proof; chromium-1228 already installed).
- **Files modified:** `desktop/package.json`

---

**Total deviations:** 4 auto-fixed (3 blocking, 1 bug)
**Impact on plan:** All auto-fixes necessary for correctness. No scope creep; no frontend or out-of-scope files touched.

## Issues Encountered

- The plan's `frontend/package.json` "typecheck" script does not exist; verified the frontend with the equivalent `npx tsc --noEmit` (exit 0). Not fixed (out of scope; noted for the verifier).
- Dev-mode default renderer URL `http://127.0.0.1:3000` requires `npm run dev` in `frontend/` until Phase 43 wires the orchestrator — documented in code.

## Security Flag Verification

| Flag | Value | Verified by |
|---|---|---|
| `sandbox` | true | smoke test 3 (bridge posture) + test 4 (no Node globals) |
| `contextIsolation` | true | smoke test 3 + test 2 (only 4 keys on `window.novelMindDesktop`) |
| `nodeIntegration` | false | smoke test 3 + test 4 (`require`/`process`/`module` undefined) |
| `webSecurity` | true | smoke test 3 |
| popups / new windows | denied | smoke test 7 (`window.open` → null) |
| external navigation | blocked | smoke test 7 (URL stays loopback) |
| raw `ipcRenderer` | absent | smoke test 4 (`typeof window.ipcRenderer === "undefined"`) |
| bootstrap payload | minimal | smoke test 5 (only appVersion/bridgeVersion/features) |

## User Setup Required

None — no external service configuration. Electron binary sourced from the local cache (same
43.3.0 artifact already used by `desktop/proof`).

## Next Phase Readiness

- Secure shell boundary is ready for Phase 42-02 (route/workflow parity and window/CSP policy
  hardening evidence) and Phase 43 (service orchestrator replaces `NOVELMIND_RENDERER_URL` env
  injection).
- Deferred to later phases: CSP header policy (42-03 scope), credentials/offline behavior (44),
  installer/signing/clean-VM qualification (45).

---
*Phase: 42-secure-desktop-shell*
*Completed: 2026-08-10*
