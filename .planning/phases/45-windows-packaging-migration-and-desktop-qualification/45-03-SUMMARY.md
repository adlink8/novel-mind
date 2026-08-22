---
phase: 45-windows-packaging-migration-and-desktop-qualification
plan: "03"
subsystem: desktop-qualification-uat
tags: [desktop, clean-vm, uat, packaged-exe, first-run, 13-routes, offline-recovery, data-preservation, qualification-manifest, no-console, single-instance, node_modules-defect]
requires:
  - "45-01 (win-unpacked + NSIS artifact, checksums, single-instance/no-console)"
  - "45-02 (upgrade/recovery/uninstall policy + prior-version fixture)"
  - "42-03 (Electron in-app route-parity + critical-workflow specs)"
  - "44-03 (offline/provider-gate capability semantics)"
provides:
  - "desktop/tests/fixtures/qualification-manifest.json — checksum-bound qualification manifest (artifact + machine boundary, clean_vm=false)"
  - "desktop/tests/clean-vm/provision.ps1 — tightened-PATH + isolated-user-data clean-machine boundary simulator (approximation, NOT pristine-VM evidence)"
  - "desktop/tests/clean-vm/run-qualification.ps1 — orchestrator: manifest/artifact gate → provision → packaged-e2e suite → evidence index; -RequireAll fail-closed"
  - "desktop/tests/clean-vm/playwright.config.ts + qualification-setup/teardown + bundled-server.ts — packaged e2e harness that serves the BUNDLED renderer through the shipped exe's embedded Node"
  - "desktop/tests/e2e/first-run.spec.ts (4), offline-recovery.spec.ts (4), z-killed-service.spec.ts (1) — packaged UAT specs"
  - "desktop/tests/e2e/launch.ts — shared launch helper; route-parity/critical-workflows now run against the packaged exe when NOVELMIND_PACKAGED_EXE is set (42-03 dev behavior preserved)"
  - ".planning/phases/45-windows-packaging-migration-and-desktop-qualification/45-UAT.md — redacted evidence index (REQ-DESK-09/10)"
  - "FIX: desktop/electron-builder.yml + desktop/scripts/build-windows.ps1 + desktop/tests/package/package-layout.test.ts — packaged artifact now ships next-standalone/node_modules (dedicated extraResources matcher + audit gate + test)"
affects:
  - "45-04 (release evidence gate consumes 45-UAT.md; clean-VM remains a blocking external gap)"
tech-stack:
  added:
    - "No new npm/PowerShell dependencies — playwright, node:child_process/node:fs, existing electron-builder"
  patterns:
    - "Checksum-bound qualification manifest consumed by a fail-closed orchestrator (artifact gate before any execution)"
    - "Honest machine boundary: tightened PATH + isolated NOVELMIND_USER_DATA simulate a clean first-run machine and are recorded as an approximation, never as clean-VM evidence"
    - "Bundled renderer served through the SHIPPED packaged exe's embedded Node (ELECTRON_RUN_AS_NODE) — the packaged adapter's own mechanism — on a dynamic loopback port"
    - "Packaged e2e specs stay identical to the 42-03 dev specs via a shared launch helper (NOVELMIND_PACKAGED_EXE seam)"
    - "Killed-service spec owns its own renderer instance and runs last (z-* naming) so mid-suite service death cannot break other specs"
key-files:
  created:
    - desktop/tests/fixtures/qualification-manifest.json
    - desktop/tests/clean-vm/provision.ps1
    - desktop/tests/clean-vm/run-qualification.ps1
    - desktop/tests/clean-vm/playwright.config.ts
    - desktop/tests/clean-vm/qualification-setup.ts
    - desktop/tests/clean-vm/qualification-teardown.ts
    - desktop/tests/clean-vm/qualification-state.ts
    - desktop/tests/clean-vm/bundled-server.ts
    - desktop/tests/e2e/first-run.spec.ts (4 tests)
    - desktop/tests/e2e/offline-recovery.spec.ts (4 tests)
    - desktop/tests/e2e/z-killed-service.spec.ts (1 test)
    - desktop/tests/e2e/launch.ts
    - .planning/phases/45-windows-packaging-migration-and-desktop-qualification/45-UAT.md
  modified:
    - desktop/electron-builder.yml (dedicated node_modules extraResources matcher)
    - desktop/scripts/build-windows.ps1 (packaged node_modules audit gate)
    - desktop/tests/package/package-layout.test.ts (packaged node_modules/next presence test)
    - desktop/tests/e2e/route-parity.spec.ts (launchShell helper, packaged-capable)
    - desktop/tests/e2e/critical-workflows.spec.ts (launchShell helper, packaged-capable)
decisions:
  - "No clean VM available/authorized → the qualification runs as a LOCAL APPROXIMATION: tightened PATH + isolated user data + bundled renderer via the packaged exe's embedded Node. Missing clean-VM execution remains a BLOCKING release-evidence gap (D-45-07/D-45-09); every UAT row records pass/fail on this machine."
  - "The 45-03 UAT found a REAL packaging defect: extraResources dropped node_modules (electron-builder's top-level node_modules filter). Fixed in-scope (electron-builder.yml + build audit + test) so the shipped artifact is actually runnable; without it the bundled renderer cannot start."
  - "The packaged main process does not yet auto-wire the bundled renderer (PackagedProcessAdapter integration is a documented post-45 prerequisite). The UAT uses the NOVELMIND_RENDERER_URL seam pointed at the bundled tree served through the packaged exe's embedded Node — the identical mechanism the packaged adapter will use."
metrics:
  started: "2026-08-10"
  completed: "2026-08-11"
  typecheck: "PASS"
  qualification: "32 passed / 0 failed (packaged win-unpacked exe) AND 32 passed / 0 failed (installed NSIS exe); also PASS with -RequireAll"
  routes: "13/13 packaged routes serve HTTP 200 + hydrate (inventory contract: exactly 13, no drift)"
  package_suite: "22 passed (45-01 regression incl. new node_modules gate)"
  update_suite: "23 passed (45-02 regression)"
  nsis_install: "silent /S install to temp dir PASS; installed app.asar + exe hashes byte-identical to the manifest; 1718 files incl. node_modules"
  dev_e2e: "pre-existing failures on raw dev standalone tree (no public/), identical on unmodified HEAD — not introduced by 45-03"
  uncommitted: true
---

# Phase 45 Plan 03: Windows Desktop Qualification UAT — Summary

## Objective (one-liner)

Execute install / first-run / 13-route / critical-flow / offline-recovery /
data-preservation UAT against the **shipped** Windows desktop artifact on this
machine (no clean VM available), build a checksum-bound qualification manifest +
reusable orchestrator, and record honest evidence — surfacing one real packaging
defect (missing `node_modules` in `extraResources`) that the 45-01 audit missed.

## Machine Boundary (honest)

**No clean Windows VM was available or authorized**, so the plan's clean-VM
requirement is executed as a **local approximation**, following the orchestrator
instructions and the 41-03 precedent:

- PATH tightened (Node/npm/npx/Python/Docker/PostgreSQL/uvicorn removed → 73
  PATH entries retained) — the packaged app and its children resolve nothing
  from PATH; the Playwright harness runs under the developer Node captured
  before tightening.
- `NOVELMIND_USER_DATA` isolated to a per-run temp dir.
- The **bundled** `next-standalone` renderer is served through the **shipped
  packaged exe's embedded Node** (`ELECTRON_RUN_AS_NODE`) on a dynamic loopback
  port — the exact mechanism the packaged adapter uses — and the packaged window
  loads it via the approved-origin seam.

`desktop/tests/fixtures/qualification-manifest.json` records `clean_vm=false`,
the machine identity, artifact checksums and the approximation boundary. Missing
clean-VM execution remains a **blocking** release-evidence gap for REQ-DESK-09/10
(D-45-07/D-45-09) — it is not marked passed anywhere.

## Deliverables

### Task 1 — Qualification manifest + provisioner (machine boundary)

- `qualification-manifest.json`: schemaVersion 1, machine (Windows 11 22631 x64,
  `clean_vm=false`, no paid resource), artifact checksums (installer, exe, asar,
  server.js hash, file count), upgrade fixture identity (9 files/1946 bytes,
  schema 1/runtime 0.1.0), and the boundary note.
- `provision.ps1`: tightens PATH, isolates user data, writes `machine-boundary.json`
  + `qualification.env.json` as redacted evidence.

### Task 2 — First-run + critical-workflow qualification vs the packaged exe

- `first-run.spec.ts` (4): one window titled NovelMind; GUI-subsystem PE (no
  console); shell hydrates against the bundled renderer; desktop bridge surface
  exactly the six declared capabilities.
- `route-parity.spec.ts` and `critical-workflows.spec.ts` now run against the
  **packaged exe** when `NOVELMIND_PACKAGED_EXE` is set, via `launch.ts`; the
  42-03 dev behavior is unchanged when it is not.
- Result: **13/13 routes HTTP 200 + hydration + client navigation (6/6 groups)**
  inside the packaged window; login + login-submit workflows pass.

### Task 3 — Offline recovery + data preservation

- `offline-recovery.spec.ts` (4): no white-screen with dead API; offline
  emulation keeps local UI interactive; provider capabilities honestly gated
  (`credentials.provider === "unavailable"` on first run — no value leaks); data
  marker survives clean shutdown + relaunch under the isolated root.
- `z-killed-service.spec.ts` (1): terminates its OWN bundled renderer instance
  (runs last, z-* naming) and asserts the hydrated window stays usable, never a
  blank body (fail-closed, D-44-07).

### Task 4 — Orchestrator + evidence index

- `run-qualification.ps1`: manifest/artifact gate (hash check) → provision →
  packaged e2e suite (tightened PATH + isolated user data) → `qualification-results.md`.
  `-RequireAll` fails closed. **32/32 PASS on win-unpacked, 32/32 PASS on the
  NSIS-installed exe, 32/32 PASS with `-RequireAll`.**
- `45-UAT.md`: redacted evidence index mapping every criterion to result + evidence.

## Verification Evidence

| Check | Result |
|---|---|
| `npm run typecheck` | PASS |
| `powershell -File desktop/tests/clean-vm/run-qualification.ps1 -Manifest desktop/tests/fixtures/qualification-manifest.json` | PASS — 32/32 |
| Same with `-RequireAll` | PASS — 32/32 |
| Qualification vs **installed** NSIS exe (temp dir, isolated user data) | PASS — 32/32; installed app.asar/exe hashes byte-identical to manifest |
| 13 routes inside the packaged window | 13/13 (inventory contract holds: exactly 13) |
| Static assets from bundled tree | `/icons/icon-192.png`, `/sw.js`, `_next/static` 200 |
| `npx playwright test --config tests/package/` | 22 passed (incl. new node_modules gate) |
| `npx playwright test --config tests/update/` | 23 passed |
| No console | packaged exe `peSubsystem === "gui"` |
| Single instance / clean process tree | one window; no orphan processes after runs (tasklist verified) |
| `git status` | only planned files + pre-existing user modifications (none touched) |

## Deviations from Plan

### Auto-fixed Issues (in-scope, Rule 1/3)

**1. [Rule 1 - Bug] Packaged artifact missing `node_modules` — bundled renderer could not start**
- **Found during:** Task 2 first-run UAT (packaged exe → bundled server).
- **Issue:** `desktop/dist/win-unpacked/resources/next-standalone` shipped only
  431 files vs 1718 staged; the entire `node_modules/` (1071 files) was dropped,
  so `server.js` failed with `Cannot find module 'next'`. Root cause:
  electron-builder's copy filter unconditionally removes a **top-level
  `node_modules` directory** from every copy including `extraResources`
  (`app-builder-lib/out/util/filter.js`). The 45-01 artifact audit only checked
  `server.js` / `public/` / `.next/static`, so the defect shipped undetected.
- **Fix:**
  - `desktop/electron-builder.yml`: exclude `node_modules` from the main
    `next-standalone` matcher and add a **dedicated matcher** whose source root
    IS `node_modules` (`from: dist/staged/next-standalone/node_modules`,
    `to: next-standalone/node_modules`).
  - `desktop/scripts/build-windows.ps1`: post-build audit now fails when
    `resources/next-standalone/node_modules/next` is absent.
  - `desktop/tests/package/package-layout.test.ts`: new test asserting
    `node_modules/next` (+ its `dist/`) ships in the packaged tree.
- **Verified:** rebuilt artifact → 1718 files in packaged tree, bundled server
  serves HTTP 200, 22/22 package suite, 32/32 qualification.

**2. [Rule 1 - Bug] Qualification manifest parsed as a string (PowerShell case-insensitive collision)**
- **Found during:** Task 1/4 orchestrator run.
- **Issue:** assigning `ConvertFrom-Json` output to `$manifest` while the param
  `$Manifest` is `[string]` coerced it via `.ToString()` — the same PowerShell
  gotcha the 45-01 summary recorded. Manifest fields came back empty.
- **Fix:** renamed the parsed object to `$manifestData` in both
  `run-qualification.ps1` and `provision.ps1`.
- **Files:** both clean-vm scripts.

**3. [Rule 3 - Blocking] Playwright harness could not run under the tightened PATH**
- **Found during:** Task 2 run.
- **Issue:** `playwright.cmd` needs `node` on PATH; the tightened PATH removed it.
- **Fix:** capture the absolute harness Node before tightening
  (`Get-Command node`) and invoke `playwright/cli.js` with it; the packaged app
  and its children still run with the tightened PATH (they resolve nothing from
  PATH by construction).

**4. [Rule 1 - Bug] Killed-service test killed the shared qualification renderer mid-suite**
- **Found during:** Task 3 run — route-parity's static-asset test then 404'd
  after the shared server died.
- **Fix:** the killed-service scenario owns its OWN renderer instance
  (`bundled-server.ts`) and runs last (`z-killed-service.spec.ts`); the shared
  setup server is untouched.

## Known Stubs

None in the shipped artifact. The packaged main process does not yet auto-wire
the bundled renderer (`PackagedProcessAdapter` in `main/index.ts`) — that is a
**documented post-45 prerequisite** (43-01/44-03/45-01 summaries), not a stub;
the UAT exercises the same mechanism via the `NOVELMIND_RENDERER_URL` seam.

## Threat Flags

None beyond the plan's threat model. The qualification harness adds no network
endpoints or auth paths; it launches the existing packaged exe and its bundled
renderer on loopback only. Evidence artifacts are redacted (machine-boundary
report contains no credentials or personal data; T-45-03-03).

## Deviated from "clean VM" requirement — honest closure note

This plan's core requirement (pristine clean-VM execution) was **not** fully
satisfied. The local approximation passed 32/32 against both the win-unpacked and
the NSIS-installed artifacts, but pristine clean-VM evidence (fresh OS, no
developer profile, no repo toolchain) remains **blocking** for the 45-04 release
evidence gate and must not be represented as passed.

## Self-Check: PASSED

- `desktop/tests/fixtures/qualification-manifest.json`, `desktop/tests/clean-vm/*`
  (7 files), `desktop/tests/e2e/{first-run,offline-recovery,z-killed-service}.spec.ts`,
  `desktop/tests/e2e/launch.ts`, `.planning/.../45-UAT.md` — all exist.
- `npm run typecheck` PASS; qualification 32/32 (win-unpacked AND installed NSIS,
  and `-RequireAll`); package 22/22; update 23/23; packaged tree 1718 files with
  node_modules; installed hashes match the manifest; `git status` shows only
  planned files plus pre-existing user modifications (none touched).
