---
phase: 45-windows-packaging-migration-and-desktop-qualification
plan: "01"
subsystem: windows-packaging-installer-single-instance
tags: [desktop, electron-builder, nsis, win-unpacked, single-instance, no-console, data-isolation, staged-runtime, reproducible-build, unsigned-qualification]
requires:
  - "41-03 (packaging GO/NO-GO + pinned runtime manifest)"
  - "42-03 (secure shell baseline)"
  - "43-04 (runtime orchestration + app-data layout)"
  - "44-03 (SSE/local-auth/credential wiring in main)"
provides:
  - "Reproducible Windows packaging contract: desktop/electron-builder.yml (appId com.novelmind.desktop, productName NovelMind, win-unpacked + NSIS x64, asar shell-only whitelist, extraResources next-standalone, signAndEditExecutable=false)"
  - "desktop/scripts/stage-runtime.ps1 — hash-pinned staging of the Next standalone tree + public/ + .next/static with per-file SHA-256 inventory; rejects unproven artifacts (T-45-01-01)"
  - "desktop/scripts/build-windows.ps1 — tsc → stage → electron-builder → artifact audit → checksums/inventory; -Verify builds twice and compares inventories"
  - "Single-instance enforcement (D-45-02/T-45-01-02): desktop/src/main/single-instance.ts lock before any runtime graph; second launch exits 0 and focuses the existing window"
  - "No console windows: packaged exe is a GUI-subsystem binary; child spawns already windowsHide (43-02)"
  - "Resource/data isolation (D-45-03): installRoot passed to the app-data layout in packaged mode; mutable state stays under %APPDATA%/NovelMind; audit proves no mutable dirs ship in install resources"
  - "docs/desktop-installation.md — installer targets, Windows 10/11 x64 matrix, unsigned limitation, single-instance/data-isolation semantics"
affects:
  - "45-02+ (upgrade/uninstall policy consumes the NSIS per-user installer + data-preserving uninstall)"
  - "Post-45 prerequisites: bundled Python/FastAPI, PostgreSQL/pgvector, vector store (41-DECISION.md PREREQ-2/3/4)"
tech-stack:
  added:
    - "electron-builder ^26.15.3 (already approved in desktop devDependencies; yml contract added)"
    - "No new npm packages — packaging scripts are PowerShell; test helpers are existing node:fs/node:child_process/playwright"
  patterns:
    - "Staged immutable runtime tree under dist/staged emitted as extraResources (never inside asar)"
    - "Pinned-hash gate at staging time (server.js == runtime-manifest.json hash) — unproven artifacts fail closed"
    - "Shell-only asar whitelist (dist/{main,runtime,data,security,shared,preload} + package.json) so build artifacts never bleed into the bundle"
    - "Windows PE Subsystem (offset 0x3c→optional+68) used as a deterministic no-console proof"
    - "NOVELMIND_USER_DATA test seam overrides app.getPath('userData') before the single-instance lock is requested"
key-files:
  created:
    - desktop/electron-builder.yml
    - desktop/scripts/stage-runtime.ps1
    - desktop/scripts/build-windows.ps1
    - desktop/src/main/single-instance.ts
    - desktop/tests/package/playwright.config.ts
    - desktop/tests/package/package-layout.test.ts (14 tests)
    - desktop/tests/package/process-behavior.windows.test.ts (7 tests)
    - desktop/tests/package/pe-subsystem.ts
    - docs/desktop-installation.md
  modified:
    - desktop/src/main/index.ts (single-instance enforcement before runtime; NOVELMIND_USER_DATA seam; packaged-mode installRoot isolation)
decisions:
  - "41 NO-GO boundary honored: only proven runtimes packaged (Electron 43.3.0 + embedded Node v24.18.1 + Next standalone); Python/PG/vector not bundled (41-DECISION.md PREREQ-2/3/4); recorded in docs + bundled-inventory.json"
  - "Unsigned local qualification: signAndEditExecutable=false; no publish/auto-update; signing/publication remain external gates (D-45-06)"
  - "Artifact targets: win-unpacked (dir) + NSIS per-user installer (oneClick=false, perMachine=false, deleteAppDataOnUninstall=false)"
  - "Windows matrix: Windows 10 + Windows 11, x64 only"
metrics:
  started: "2026-08-10"
  completed: "2026-08-11"
  typecheck: "PASS"
  package_tests: "21 passed / 0 failed (14 layout + 7 process-behavior)"
  staged_inventory: "1440 files, 32.4 MB, server.js hash matches pinned proof hash"
  reproducibility: "two full builds → identical staged inventories (1440 files)"
  artifacts: "dist/win-unpacked (NovelMind.exe + app.asar + resources/next-standalone) + dist/NovelMind-Setup-0.1.0-x64.exe (217.7 MB) + CHECKSUMS.SHA256 + bundled-inventory.json"
  uncommitted: true
---

# Phase 45 Plan 01: Windows Packaging Migration and Desktop Qualification — Summary

## Objective (one-liner)

Phase 45 wave-0 packaging baseline: a reproducible, hash-pinned Windows artifact
(win-unpacked + NSIS) that is single-instance, console-free and keeps all mutable
data outside install resources — strictly bounded to the Phase 41-proven runtimes.

## 41 NO-GO Boundary Handling

`41-DECISION.md` is **NO-GO** (unchanged, not edited). Per the orchestrator
authorization (`gate_overrides.phase_42_45_execution`) and the 41-01-approved
electron-builder dependency, this plan packages **only the proven runtimes**:

- ✅ Electron 43.3.0 + embedded Node v24.18.1 (ELECTRON_RUN_AS_NODE, 41 prerequisite #1 proven)
- ✅ Next standalone renderer tree (server.js hash pinned to `8120c099…`, the exact 13-route parity artifact)
- ❌ Bundled Python/FastAPI, PostgreSQL/pgvector, vector store — **not packaged**;
  recorded in `docs/desktop-installation.md` and `dist/bundled-inventory.json`
  as post-45 prerequisites (41-DECISION.md PREREQ-2/3/4). The packaged app fails
  closed for every component except `next` (`PackagedProcessAdapter`).

Task 1's automated gate (`Select-String '^Verdict: GO$'`) therefore cannot pass;
that is expected and handled per the orchestrator's instructions — the decision
is documented honestly rather than falsified.

## Deliverables

### Task 1 — Installer configuration (approved scope)

- `desktop/electron-builder.yml`: `appId com.novelmind.desktop`, `productName NovelMind`,
  targets `dir` (win-unpacked) + `nsis` (x64), `asar: true` with an explicit
  shell-only files whitelist, `extraResources: dist/staged/next-standalone →
  resources/next-standalone`, `signAndEditExecutable: false`, NSIS per-user
  (`perMachine: false`, `deleteAppDataOnUninstall: false` — data preserved), no
  `publish`/auto-update section.
- `docs/desktop-installation.md`: artifact table, Windows 10/11 x64 matrix, the
  unsigned-test limitation (SmartScreen "unknown publisher"), single-instance and
  data-isolation semantics, and the honest 41 NO-GO packaged-runtime boundary.

### Task 2 — Stage immutable runtimes + deterministic artifact

- `desktop/scripts/stage-runtime.ps1`: verifies the source server.js SHA-256
  against the pinned proof-manifest hash (fail-closed), stages the standalone
  tree + `public/` + `.next/static` (missing assets fail), excludes `*.map`,
  emits `dist/staged/staged-manifest.json` (per-file sha256/size inventory) and
  `CHECKSUMS.SHA256`. `-VerifyOnly` re-hashes the staged tree and compares it to
  the declared inventory.
- `desktop/scripts/build-windows.ps1`: `npm run build` → stage → electron-builder
  `--win --publish never` → post-build audit (exe + app.asar + staged server.js
  hash) → `dist/CHECKSUMS.SHA256` + `dist/bundled-inventory.json`. `-Verify`
  builds twice and compares staged inventories for reproducibility.

### Task 3 — Single-instance + clean process behavior

- `desktop/src/main/single-instance.ts`: `enforceSingleInstance` calls
  `app.requestSingleInstanceLock()` at module load — before any runtime graph or
  window; a duplicate exits immediately and the primary focuses/restores the
  existing window on `second-instance`. Pure `focusMainWindow` decision logic is
  unit-testable.
- `desktop/src/main/index.ts`: wires the lock at the top of the main process,
  honors the `NOVELMIND_USER_DATA` test seam *before* the lock request (so the
  lock scopes to the overridden root), and passes `app.isPackaged ? dirname(process.execPath)
  : undefined` as `installRoot` to the app-data layout — packaged install-root
  overlap fails closed (D-45-03/D-43-05).
- No console: packaged `NovelMind.exe` is a GUI-subsystem PE (verified); child
  processes spawn with `windowsHide` (43-02).

### Tests (`desktop/tests/package/`, 21 passed)

- `package-layout.test.ts` (14): yml contract (appId/productName, targets, asar,
  unsigned, data-preserving uninstall, no publish); staged inventory pins match
  the Phase 41 proof manifest; every staged file reproduces its SHA-256; staged
  tree self-contained (node_modules/server.js/static/public, no download
  prerequisite); no `.map`/`.env`/`.pem`/`.key` in resources; no fixed packaged
  port (server.js binds `process.env.PORT`); app-data root disjoint from the
  win-unpacked install root; no mutable-state dirs (`pgdata/data/logs/backups/
  secrets/uploads/storage/artifacts/chroma`) in install resources; win-unpacked
  contains exe + app.asar + exact staged server.js; exe is GUI-subsystem.
- `process-behavior.windows.test.ts` (7): 4 pure `focusMainWindow` tests
  (null/destroyed/minimized/normal); 3 real-process tests — primary creates
  exactly one window and stays alive; a second launch sharing the same userData
  exits immediately (code 0) without a second window; after a clean primary exit
  the lock is released and a later launch becomes primary.
- Suite runs against the **packaged** `win-unpacked` exe when a build exists,
  otherwise a freshly recompiled dev shell (`tsc` is always re-run so `dist/` is
  never stale).

## Verification Evidence

| Check | Result |
|---|---|
| `cd desktop && npm run typecheck` | PASS |
| `powershell -File desktop/scripts/build-windows.ps1` | PASS — win-unpacked + `NovelMind-Setup-0.1.0-x64.exe` (217.7 MB) |
| `powershell -File desktop/scripts/build-windows.ps1 -Verify` | PASS — two staged inventories identical (1440 files) |
| `npx playwright test --config tests/package/` | 21 passed / 0 failed |
| Artifact audit | `win-unpacked/NovelMind.exe` + `resources/app.asar` (shell-only, 44 entries) + `resources/next-standalone/server.js` (hash `8120c099…` matches pinned) |
| No console | `NovelMind.exe` PE Subsystem = 2 (GUI); children `windowsHide` |
| No fixed port | packaged server.js binds `process.env.PORT` (OS-allocated loopback) |
| Single-instance | second launch exits 0, one window only; clean exit releases lock |
| Mutable-path audit | no `pgdata/data/logs/…` in staged resources; app-data root disjoint from install root |
| `git status` | only planned files + pre-existing user modifications |

## Deviations from Plan (all auto-fixed, Rule 1/3)

**1. [Rule 1 - Bug] PowerShell `$Manifest`/`$manifest` case-insensitive collision**
- Found during: Task 2 verify.
- Issue: `param([string]$Manifest)` makes `$manifest` (case-insensitive) a
  `[string]` variable; assigning the inventory hashtable coerced it via
  `.ToString()` to the literal `"System.Collections.Hashtable"`, so the JSON
  manifest was a 30-char type-name string.
- Fix: renamed the local hashtable to `$inventoryManifest` (with an explanatory
  comment).
- Files: `desktop/scripts/stage-runtime.ps1`.

**2. [Rule 1 - Bug] PowerShell 5.1 UTF-8 BOM breaks strict JSON parsing**
- Found during: Task 4 test run.
- Issue: `Set-Content -Encoding utf8` writes a BOM; `JSON.parse` rejects it.
- Fix: write manifests/checksums with `[System.Text.UTF8Encoding]($false)` /
  ASCII via `[System.IO.File]::WriteAllText` (both scripts).
- Files: `desktop/scripts/stage-runtime.ps1`, `desktop/scripts/build-windows.ps1`.

**3. [Rule 1 - Bug] Next server source maps shipped in staged resources**
- Found during: Task 4 test run.
- Issue: `frontend/.next/standalone/.next/server/**/*.map` (16 files) leaked
  server source into the bundle (T-45-01-01).
- Fix: `robocopy … /XF *.map` in the standalone-tree copy.
- Files: `desktop/scripts/stage-runtime.ps1`.

**4. [Rule 1 - Bug] `files: dist/**/*` packed the staged tree (and prior build artifacts) into asar**
- Found during: win-unpacked asar inspection.
- Issue: the broad whitelist pulled `dist/staged/next-standalone` into `app.asar`
  (duplicating extraResources) and would swallow `dist/win-unpacked` +
  `dist/*.exe` on repeat builds — a determinism hazard.
- Fix: explicit shell-only whitelist (`dist/{main,runtime,data,security,shared,
  preload}` + `package.json`); asar re-audited to 44 entries, staged tree only in
  `resources/next-standalone`.
- Files: `desktop/electron-builder.yml`.

**5. [Rule 3 - Blocking] Stale dev `dist/` broke the single-instance suite**
- Found during: Task 3/4 verify.
- Issue: the dev-mode fallback used a pre-built `dist/main/index.js` compiled
  before the single-instance wiring, so a second launch stayed alive.
- Fix: always recompile with `tsc -p tsconfig.build.json` before dev-mode launch.
- Files: `desktop/tests/package/process-behavior.windows.test.ts`.

**6. [Rule 3 - Blocking] `rmSync` EPERM on the temp userData dir after app close**
- Found during: Task 4 verify.
- Issue: the just-closed Electron child briefly holds the userData dir, so
  best-effort temp cleanup raced with it.
- Fix: bounded retry loop before giving up.
- Files: `desktop/tests/package/process-behavior.windows.test.ts`.

## Known Stubs

None. The packaged app intentionally does not reach a full runtime graph
(only `next` is launchable) — that is the documented 41 NO-GO boundary, not a stub.

## Threat Flags

None beyond the plan's threat model: no new network endpoints, auth paths,
file-access patterns or trust-boundary changes were introduced. `installRoot`
isolation adds a fail-closed guard (T-45-01-01/02/03 mitigation).

## Self-Check: PASSED

- `desktop/electron-builder.yml`, `desktop/scripts/stage-runtime.ps1`,
  `desktop/scripts/build-windows.ps1`, `desktop/src/main/single-instance.ts`,
  `desktop/tests/package/*` (4 files), `docs/desktop-installation.md` — all exist.
- `npm run typecheck` PASS; package suite 21/21 PASS; build + `-Verify` PASS;
  staged server.js hash matches the pinned proof hash; `git status` shows only
  planned files plus pre-existing user modifications (none touched).
