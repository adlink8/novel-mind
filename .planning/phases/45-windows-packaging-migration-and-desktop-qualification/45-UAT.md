# Phase 45 — Windows Desktop Qualification UAT Evidence Index (Plan 45-03)

**Wave:** 2 · **Plan:** 45-03 · **Type:** execute
**Date:** 2026-08-10/11
**Requirement:** REQ-DESK-09 / REQ-DESK-10
**Boundary (fail-closed):** this run executed on the **developer workstation**, NOT
a pristine Windows VM. Missing clean-VM execution remains a **blocking** gap for
release evidence (D-45-07 / D-45-09). Every row below records pass/fail **on this
machine**; rows that a clean VM would cover are marked `[approximation]`.

## Machine Boundary

| Item | Value |
|---|---|
| OS | Windows 11 Pro 10.0.22631 (22H2) x64 |
| clean_vm | **false** (no VM available/authorized) |
| VM image / snapshot | none |
| Paid/external resources | none |
| PATH tightening | Node/npm/npx/Python/Docker/PostgreSQL/uvicorn tokens removed (73 PATH entries retained) |
| User-data isolation | `NOVELMIND_USER_DATA` → per-run temp dir |
| Renderer mechanism | bundled `next-standalone` served through the **shipped packaged exe**'s embedded Node (`ELECTRON_RUN_AS_NODE`), dynamic loopback port |
| Evidence artifacts | `desktop/tests/clean-vm/results/machine-boundary.json`, `qualification-results.md`, `qualification.env.json` |

## Artifact Under Test (checksum-bound)

| Artifact | SHA-256 |
|---|---|
| `desktop/dist/NovelMind-Setup-0.1.0-x64.exe` | `8ee486cd…ed46a` |
| `desktop/dist/win-unpacked/NovelMind.exe` | `09b11247…db197` |
| `desktop/dist/win-unpacked/resources/app.asar` | `a0bf12b6…d804` |
| bundled `resources/next-standalone/server.js` | `8120c099…3f8a` |
| `resources/next-standalone` file count | 1718 (incl. `node_modules/`, 1071 deps) |

Source of truth: `desktop/dist/CHECKSUMS.SHA256` (45-01 reproducible build).

## UAT Rows

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Installer installs on this machine without pre-installed runtimes | **PASS** | NSIS `/S` silent install into an isolated temp dir completed; installed tree = 1718 files, app.asar + exe hashes byte-identical to the manifest |
| 2 | First run: one window, titled NovelMind, no console | **PASS** | packaged exe: `BrowserWindow.getAllWindows() === 1`; page title contains "NovelMind"; exe is GUI-subsystem PE (`peSubsystem === "gui"`) |
| 3 | First run: shell hydrates against the bundled renderer | **PASS** | packaged window renders login gate (回到你的故事里 / 用户名 / 密码) |
| 4 | First run: desktop bridge + preload contract loaded | **PASS** | `window.novelMindDesktop` exposes exactly the six declared capabilities |
| 5 | All 13 routes serve HTTP 200 + hydrate inside the packaged window | **PASS** | 13/13 routes (8 static + 5 dynamic); frozen inventory contract held (count = 13, no drift) |
| 6 | Client navigation via the app shell sidebar (route groups) | **PASS** | 6/6 route-group navigations land and re-hydrate |
| 7 | Static assets served from the bundled standalone tree | **PASS** | `/icons/icon-192.png`, `/sw.js`, `_next/static` chunks all 200 inside the packaged window |
| 8 | Critical workflow: login page reachable | **PASS** | login form renders in the packaged window |
| 9 | Critical workflow: login submit renders main navigation | **PASS** | 主导航 + six sidebar entries render after mocked login |
| 10 | Offline: window never white-screens with the API surface unavailable | **PASS** | dead API → login gate still renders, body non-empty |
| 11 | Offline emulation keeps local UI interactive (`navigator.onLine` false) | **PASS** | offline → local UI still usable, no fabricated provider success |
| 12 | Provider capabilities honestly gated on first run | **PASS** | redacted bootstrap `credentials.provider === "unavailable"` (未配置 AI 提供商); no value leaks |
| 13 | Killed-service recovery (fail-closed, no white screen) | **PASS** | after terminating the bundled service, the hydrated window keeps its UI; navigation surfaces an honest error surface, never a blank body |
| 14 | Data preservation across restart | **PASS** | a marker under the isolated `%APPDATA%`-equivalent root survives clean shutdown + relaunch of the packaged exe |
| 15 | Clean shutdown owns its process tree | **PASS** | no orphan NovelMind/electron processes remain after the suite (verified via tasklist) |

## Execution Logs (redacted — no credentials/personal data)

- `desktop/tests/clean-vm/results/qualification-results.md` — playwright exit 0, 32/32 PASS (also run with `-RequireAll`, 32/32 PASS).
- `desktop/tests/clean-vm/results/machine-boundary.json` — provisioned PATH + isolation report.
- Playwright output is captured inline by `run-qualification.ps1`; screenshots for failures are retained in `desktop/test-results/` only when a run fails (all runs here passed).

## Regression Baselines (not clean-VM evidence)

- `npx playwright test --config tests/package/` — **22 passed** (45-01 package/process qualification, incl. new packaged `node_modules` gate).
- `npx playwright test --config tests/update/` — **23 passed** (45-02 upgrade/recovery/uninstall).
- Dev-mode e2e (`route-parity`/`critical-workflows` via the repo `playwright.config.ts`): the **unmodified HEAD versions fail identically** (2 pre-existing dev-tree issues: the raw `frontend/.next/standalone` lacks `public/`, so `/icons/icon-192.png` 404s and the login gate relies on a stale standalone build). Verified by running the original 42-03 specs from `git show HEAD` — same 2 failures. **Not introduced by 45-03.**

## Found & Fixed During UAT (packaging defect)

The shipped artifact was missing `resources/next-standalone/node_modules` (431 vs 1718 staged files), so the bundled Next server could not start (`Cannot find module 'next'`). Root cause: electron-builder's copy filter unconditionally drops a top-level `node_modules` directory from every copy, including `extraResources` (`app-builder-lib/out/util/filter.js`). Fix: dedicated `extraResources` matcher whose source root IS `node_modules` + a hardened artifact audit. See 45-03-SUMMARY.md.

## Known Stubs / Not Covered Here

- Packaged main-process auto-wiring of the bundled renderer (`PackagedProcessAdapter` in `main/index.ts`) remains a **documented post-45 prerequisite** (43-01/44-03/45-01 summaries). The UAT uses the `NOVELMIND_RENDERER_URL` seam pointed at the bundled tree served through the packaged exe's embedded Node — the same mechanism the packaged adapter will use.
- Pristine clean-VM execution (fresh OS, no developer profile, no repo toolchain) is **not** covered and remains a **blocking** release-evidence gap.
