---
phase: 42-secure-desktop-shell
plan: 03
subsystem: desktop-shell-parity
tags: [electron, playwright, route-parity, renderer-privileges, negative-tests, capabilities]

# Dependency graph
requires:
  - phase: 42-secure-desktop-shell
    plan: 01
    provides: Secure BrowserWindow factory, DesktopBridge contract, Playwright shell harness (globalSetup/teardown)
  - phase: 42-secure-desktop-shell
    plan: 02
    provides: CSP/navigation/window/permission deny-by-default policy + sender-validated IPC registration layer
  - phase: 41-electron-architecture-and-packaging-proof
    plan: 02
    provides: Frozen 13-route inventory (route-inventory.json) + browser parity assertion pattern
provides:
  - Typed optional desktop capability consumption (`@/lib/desktop/capabilities`) with deterministic browser-mode unsupported state
  - Electron in-app 13-route parity suite (HTTP 200 + markers/test-ids + shell hydration + client navigation inside the shell window)
  - Critical workflow smoke inside Electron (login page reachable, login submit renders main navigation)
  - Renderer-privilege negative suite (exact bridge surface, Node globals unreachable, popup/external nav blocked)
  - Release-blocking E2E evidence for REQ-DESK-01 / REQ-DESK-02 / T-42-03-01 / T-42-03-02
affects: [42-04, 42-05, 43]

# Tech tracking
tech-stack:
  added: [] # no new dependencies
  patterns:
    - "window.novelMindDesktop typed via type-only import of the shared bridge contract; resolved only through @/lib/desktop/capabilities — never by importing Electron or probing Node globals"
    - "Electron context.route() API mocks registered before firstWindow so initial load and every navigation see the same deterministic API surface (auth/me fixture user -> shell renders; other /api + /agent -> 404 error states)"
    - "renderer-privilege negatives reused from the shell smoke suite but asserted as the route-parity task's own gate (T-42-03-01)"
    - "frozen inventory consumed through desktop/tests/fixtures/routes.ts so Electron parity compares against the identical 13-route surface (a missing inventory file fails the suite)"

key-files:
  created:
    - desktop/tests/e2e/route-parity.spec.ts
    - desktop/tests/e2e/critical-workflows.spec.ts
    - desktop/tests/e2e/renderer-privileges.spec.ts
  modified:
    - desktop/tests/fixtures/routes.ts
  verified-exists:
    - frontend/src/types/desktop-bridge.d.ts
    - frontend/src/lib/desktop/capabilities.ts
    - frontend/src/lib/desktop/capabilities.test.ts
  deleted:
    - desktop/tests/probe-route.spec.ts

key-decisions:
  - "The throwaway probe (probe-route.spec.ts) proved Electron context.route interception and auth/me shell rendering; its findings are promoted into the formal e2e suites and the probe itself is deleted"
  - "Formal Electron suites follow the plan's files_modified list (tests/e2e/route-parity.spec.ts, critical-workflows.spec.ts, renderer-privileges.spec.ts) rather than a route-parity.electron.* filename"
  - "A single Electron app is launched once per suite file (beforeAll) and reused serially, matching shell-smoke/policy/ipc conventions and the workers:1 harness"
  - "Login workflow uses a mutable mock: /api/auth/me returns 401 pre-login (login page deterministic), POST /api/auth/login flips the mock to authenticated (shell renders after a real form submit)"

requirements-completed: [REQ-DESK-01, REQ-DESK-02]

# Metrics
duration: 1.2h
completed: 2026-08-10
---

# Phase 42 Plan 03: Electron Route, Workflow and Renderer-Privilege Parity Summary

**Electron in-app parity for the full product surface: all 13 frozen routes serve/hydrate/navigate inside the production shell window, the critical login workflow drives the shell to render its main navigation through a real form submit, and renderer privileges are proven not to have grown — 20 new E2E tests (plus the 8-test Task 1 capability resolver unit suite), all green twice.**

## One-liner

Typed optional desktop capability consumption with deterministic browser-mode unsupported state; formal Electron in-app route parity (13 routes), critical login workflow smoke, and renderer-privilege negatives — all running inside the real shell window via `_electron.launch`, green on two consecutive full-suite runs.

## Task 1 — Typed optional desktop capability consumption (pre-verified, carried forward)

- `frontend/src/types/desktop-bridge.d.ts`: declares `window.novelMindDesktop?: DesktopBridge` via a type-only import of `desktop/src/shared/bridge-contract` (single source of truth across the trust boundary; erased at compile time, no Electron code in the web bundle).
- `frontend/src/lib/desktop/capabilities.ts`: optional resolver — `isDesktop` / `bridge` getters plus `getRuntimeStatus` / `getBootstrap` / `openExternalLink` / `requestRuntimeRestart` / `onRuntimeStatus` wrappers. Browser mode (no bridge) returns `{ supported: false, reason: "bridge-unavailable" }` deterministically; a malformed bridge object is treated as absent (fail closed).
- `frontend/src/lib/desktop/capabilities.test.ts`: 8 unit tests (browser degradation, Electron forwarding, malformed bridge fail-closed) — **8/8 PASS** via `npx vitest run src/lib/desktop/`.
- No route/component imports Electron: acceptance `rg` gate clean.

## Task 2 — Route, workflow and renderer-privilege parity (this session)

- `desktop/tests/fixtures/routes.ts` (carried forward, verified): loads the SAME frozen inventory `desktop/proof/tests/route-inventory.json` (13 routes) the Phase 41 proof consumed; `concretePath` renders `[id]`/`[setId]` templates. A missing inventory file fails the suite deterministically.
- `desktop/tests/e2e/route-parity.spec.ts` (created): launches the Electron shell (`_electron.launch`), registers context routes before `firstWindow` (auth/me -> fixture user, other /api + /agent -> 404), then asserts: frozen inventory contract (exactly 13 routes, unique), static assets served from the standalone tree inside Electron (public icons + sw.js + every `_next/static` chunk referenced by root HTML), each of the 13 routes serving HTTP 200 with markers/test-ids hydrated and the shell nav present, and one client navigation per route group via the app-shell sidebar (工作台/书架/分析/评测/创作/设置中心).
- `desktop/tests/e2e/critical-workflows.spec.ts` (created): login page reachable inside the Electron window (回到你的故事里 + 用户名/密码 fields + 登录 button) and login submit rendering the main navigation (主导航 + all six sidebar entries) via the mutable auth mock.
- `desktop/tests/e2e/renderer-privileges.spec.ts` (created): bridge exposes exactly the five declared capabilities and nothing else; require/process/module/ipcRenderer all undefined and every require attempt (electron/fs/child_process/path) throws; window.open popups blocked (window count stays 1); external navigation cannot leave the approved loopback origin.

## Test, Fix, and Confirm

- Desktop E2E suite run **twice**, both fully green. Full harness now **56/56 PASS ×2** (8 smoke + 9 policy + 12 IPC + 27 new e2e).
- Desktop `tsc --noEmit`: **0 errors**. Frontend `tsc --noEmit`: **0 errors**. Frontend capability resolver unit suite: **8/8 PASS**.
- Probe deletion confirmed: `desktop/tests/probe-route.spec.ts` removed; not present in working tree or git status.

## Test Numbers

| Suite | Tests | Result |
|---|---|---|
| shell-smoke.spec.ts (42-01) | 8 | PASS ×2 |
| security/policy.spec.ts (42-02) | 9 | PASS ×2 |
| security/ipc.spec.ts (42-02) | 12 | PASS ×2 |
| e2e/route-parity.spec.ts (42-03) | 21 (1 contract + 1 static + 13 routes + 6 nav) | PASS ×2 |
| e2e/critical-workflows.spec.ts (42-03) | 2 | PASS ×2 |
| e2e/renderer-privileges.spec.ts (42-03) | 4 | PASS ×2 |
| **Desktop total** | **56** | **PASS ×2** |
| frontend/src/lib/desktop/capabilities.test.ts | 8 | PASS |

## Files Created/Modified (this plan)

| File | Lines | Purpose |
|---|---|---|
| `frontend/src/types/desktop-bridge.d.ts` | 25 | Typed `window.novelMindDesktop` via type-only import of shared contract |
| `frontend/src/lib/desktop/capabilities.ts` | 112 | Optional desktop capability resolver (fail-closed, browser-safe) |
| `frontend/src/lib/desktop/capabilities.test.ts` | 126 | 8 unit tests: browser degradation + Electron forwarding + malformed bridge |
| `desktop/tests/fixtures/routes.ts` | 64 | Loads frozen 13-route inventory for the Electron parity suite |
| `desktop/tests/e2e/route-parity.spec.ts` | 169 | 21 tests: inventory contract + static assets + 13 routes + 6 client navigations inside Electron |
| `desktop/tests/e2e/critical-workflows.spec.ts` | 122 | 2 tests: login page reachable, login submit renders main navigation |
| `desktop/tests/e2e/renderer-privileges.spec.ts` | 124 | 4 tests: exact bridge surface, Node globals unreachable, popup/external-nav blocked |
| `desktop/tests/probe-route.spec.ts` | deleted | Throwaway probe, findings promoted into formal suites |

New files: 4 desktop spec/fixture files (478 lines) + 3 Task 1 frontend files (263 lines).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Plan verify commands reference `tests/e2e` and a double-run of the frontend browser suite**
- **Found during:** Task 2/3 verify
- **Issue:** the plan's `<verify>` blocks name `npx playwright test tests/e2e` and `npm test -- --run` (no such script in frontend). The desktop `testMatch` is already open (`**/*.spec.ts`) so the full harness runs the new suites; this session ran the full `npx playwright test` twice (the equivalent of the plan's double-run) and `npx vitest run src/lib/desktop/` for the frontend unit gate.
- **Fix:** used `npx playwright test` (full harness, twice) and `npx vitest run src/lib/desktop/`. **Files modified:** none.

**2. [Rule 3 - Blocking] `noUncheckedIndexedAccess` rejects `m[1]` from `matchAll`**
- **Found during:** desktop typecheck after writing route-parity.spec.ts
- **Issue:** desktop `tsconfig` has `noUncheckedIndexedAccess: true`; `[...html.matchAll(...)].map(m => m[1])` yields `string | undefined` (TS2345).
- **Fix:** added a `.filter((s): s is string => s !== undefined)` guard. **Files modified:** `desktop/tests/e2e/route-parity.spec.ts`.

**3. [Rule 3 - Blocking] Module-scoped `let rendererUrl` loses narrowing in arrow closures**
- **Found during:** desktop typecheck
- **Issue:** assigning `rendererUrl = envUrl` inside `beforeAll` does not narrow the module-level `string`-typed variable inside test closures under strict mode.
- **Fix:** hoisted `const envUrl = process.env...` locally in `beforeAll` and assigned it to the module variable. **Files modified:** `desktop/tests/e2e/route-parity.spec.ts`, `desktop/tests/e2e/critical-workflows.spec.ts`.

---

**Total deviations:** 3 auto-fixed (all Rule 3 — test/type correctness; no scope creep; no frontend or out-of-scope files touched).
**Impact on plan:** All fixes are mechanical; plan executed as written otherwise.

## Issues Encountered

- The Next standalone renderer logs `ECONNREFUSED 127.0.0.1:8010` (frontend `/api` proxy target) during suites because no backend runs in the harness — unrelated to the shell, pre-existing (also recorded in 42-02).
- Playwright's `_electron.launch` defaults to the system-installed Chromium for a session-scoped browser; suites are serial (workers:1) so the single app instance and mock state are deterministic. No test leaks across suites.

## Security Flag Verification

| Item | Value | Verified by |
|---|---|---|
| Bridge surface (renderer) | exactly 5 capabilities, nothing else | renderer-privileges test 1 |
| Node globals (require/process/module) | undefined; every require attempt throws | renderer-privileges test 2 |
| Raw ipcRenderer | undefined | renderer-privileges test 2 |
| window.open popups | null, no extra window | renderer-privileges test 3 |
| External navigation | blocked, stays loopback | renderer-privileges test 4 |
| Route surface | 13 routes, frozen inventory, no drift | route-parity contract test |
| Static assets | public assets + every root-referenced `_next/static` chunk 200 | route-parity static test |
| Route hydration | markers/test-ids + shell nav present on all 13 routes | route-parity per-route tests |
| Client navigation | per-group sidebar transition inside Electron | route-parity nav tests |
| Critical workflow | login page reachable; submit renders main navigation | critical-workflows tests |
| Electron imports outside desktop/ | none (rg clean) | Task 1 acceptance gate |

## User Setup Required

None — no new dependencies, no external services.

## Next Phase Readiness

- 42-03 closes the Phase 42 parity/evidence loop: renderer surface is proven unchanged (T-42-03-01) and the product surface works inside the secure shell (T-42-03-02).
- 42-04/42-05 (desktop transport credentials, offline behavior) can reuse the Electron in-app E2E harness (context-route mocks + serial shell launch) for their own parity/negative suites.
- Phase 43 (managed local runtime/data lifecycle) replaces the env-injected renderer URL the harness still depends on (`NOVELMIND_SMOKE_RENDERER_URL`).

---
*Phase: 42-secure-desktop-shell*
*Completed: 2026-08-10*

## Self-Check: PASSED

- FOUND `desktop/tests/e2e/route-parity.spec.ts`, `critical-workflows.spec.ts`, `renderer-privileges.spec.ts`, `desktop/tests/fixtures/routes.ts`
- FOUND `frontend/src/types/desktop-bridge.d.ts`, `frontend/src/lib/desktop/capabilities.ts`, `frontend/src/lib/desktop/capabilities.test.ts`
- FOUND `.planning/phases/42-secure-desktop-shell/42-03-SUMMARY.md`
- CONFIRMED `desktop/tests/probe-route.spec.ts` deleted
- CONFIRMED desktop `tsc --noEmit` 0, frontend `tsc --noEmit` 0, desktop `npx playwright test` 56/56 ×2, frontend `npx vitest run src/lib/desktop/` 8/8
