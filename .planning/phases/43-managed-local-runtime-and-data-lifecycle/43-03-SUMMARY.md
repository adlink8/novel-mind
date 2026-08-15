---
phase: 43-managed-local-runtime-and-data-lifecycle
plan: "03"
subsystem: desktop-data-lifecycle
tags: [desktop, data-lifecycle, migration, backup, app-data, versioned-layout]
requires:
  - "43-01 (DesktopRuntime deep module + MigrationGate contract)"
provides:
  - "Versioned %APPDATA%/NovelMind layout (data/logs/backups/runtime/secrets + migration.json)"
  - "Backup-first migration transaction (MigrationRunner) with journal-resumable retries"
  - "Manifest/hash-backed backup + restore with bounded retention"
  - "MigrationGate adapter wiring the runner into the runtime's migrating state"
  - "backend storage_dir config item (NOVELMIND_STORAGE_DIR) for packaged-mode storage redirect"
affects:
  - "43-04 (renderer recovery actions; restoreBackup/openDiagnostics)"
  - "desktop/src/runtime/desktop-runtime.ts (migrating state now consumes the real gate)"
  - "docs/desktop-data-lifecycle.md (new human-facing data lifecycle policy)"
tech-stack:
  added:
    - "No new npm dependencies (Playwright + node:crypto/fs builtins)"
  patterns:
    - "Injected DataFs seam (real nodeDataFs + deterministic FakeDataFs fault injection)"
    - "Atomic metadata commit via tmp+rename; journal as retry cursor"
    - "Backup-first transaction: evidence precedes version advancement"
    - "Path authority module (containPath) rejecting traversal/install-root overlap"
key-files:
  created:
    - desktop/src/data/app-data-layout.ts (290 lines)
    - desktop/src/data/version-state.ts (91 lines)
    - desktop/src/data/backup.ts (323 lines)
    - desktop/src/data/migration-runner.ts (491 lines)
    - desktop/tests/data/app-data-layout.test.ts (198 lines)
    - desktop/tests/data/migration-recovery.test.ts (500 lines)
    - desktop/tests/data/fake-data-fs.ts (234 lines)
    - desktop/tests/data/playwright.config.ts (21 lines)
    - docs/desktop-data-lifecycle.md (85 lines)
  modified:
    - backend/app/config.py (added storage_dir config item; user's other in-tree edits untouched)
decisions:
  - "App-data root is Electron app.getPath('userData') (%APPDATA%/NovelMind); layout contract version APP_DATA_LAYOUT_VERSION=1 recorded in migration.json."
  - "Backup/restore uses manifest + sha256 per file; retries reuse only hash-verified evidence and fail typed (BACKUP_FAILED) on corruption rather than silently re-backing up."
  - "Migration steps are injected and fixed-order (files → database → vector → app_metadata); the built-in files step verifies post-copy hashes."
  - "Version metadata commits atomically (tmp+rename); a failed write leaves the previous committed state untouched."
  - "storage_dir defaults to '' (preserving the existing CWD 'storage' fallback) and is redirected via NOVELMIND_STORAGE_DIR in packaged mode."
metrics:
  duration_minutes: 95
  completed_at: "2026-08-10"
---

# Phase 43 Plan 03: Versioned App-Data Layout, Backup-First Migration and Recovery — Summary

Established the single versioned `%APPDATA%/NovelMind` layout for all mutable desktop state and a
backup-first, journal-resumable migration transaction that can never strand or discard user data:
backup evidence precedes version advancement, the committed version is written atomically, and every
injected fault (denied writes, low disk, corrupt backup, interrupted migration) yields a typed failure
with old data preserved — never a ready state from a partial migration (D-43-05/D-43-06, T-43-03-01/02/03).

## Implemented

### Versioned app-data layout (`desktop/src/data/app-data-layout.ts`, `version-state.ts`)

- Single explicit root `app.getPath('userData')` (`%APPDATA%/NovelMind`); children `data/`, `logs/`,
  `backups/`, `runtime/`, `secrets/` plus `migration.json`. `buildAppDataPaths` is pure; it rejects a
  relative root, rejects root/install-root overlap in either direction, and every mutable write target
  flows through `containPath` (normalized absolute paths, traversal rejected, absolute segments rejected).
- `initializeAppDataPaths` is idempotent (safe to run on every startup) and surfaces typed
  `WRITE_DENIED` when the directories cannot be created.
- `DataFs` is an injected filesystem seam (real `nodeDataFs` on `node:fs/promises`; `statFreeBytes`
  via `fs.statfs`); the data module never touches `node:fs` directly, enabling deterministic fault
  injection.
- `version-state.ts` reads missing/corrupt metadata as "uninitialized" (schema 0, never a crash) and
  commits `migration.json` atomically (tmp + rename) so a failed write leaves the previous committed
  state untouched.

### Backup-first migration (`desktop/src/data/backup.ts`, `migration-runner.ts`)

- `createBackup` snapshots every file under `data/` into `backups/<txnId>/` with a sha256 manifest;
  it fails EXPLICITLY with `INSUFFICIENT_SPACE` before writing any byte when the volume cannot hold
  the snapshot (T-43-03-03), and prunes to a bounded retention (default 5, newest kept).
- `verifyBackup` recomputes hashes before any retry reuses evidence; `restoreBackup` copies the
  verified snapshot back over `data/`.
- `MigrationRunner` runs the transaction: BACKUP → MIGRATE (declared steps in fixed order
  `files → database → vector → app_metadata`) → COMMIT (atomic version write). A `files` step builder
  (`createFilesCopyStep`) copies read-only install resources into app-data and re-hashes each copy.
- `MigrationFailure` is typed (code + step + txnId + backupDirPath + bounded recovery instruction);
  `oldDataPreserved` is literally `true` as a const type. The runtime is never marked ready from a
  partial migration (D-43-06).
- `runtime/migration-journal.json` is the retry cursor: an interrupted/failed attempt resumes from its
  verified backup and the first un-done step; retries do not re-backup and do not re-run completed
  steps. A journal whose backup manifest is unreadable/corrupt fails typed (`BACKUP_FAILED`) — never a
  silent re-backup.
- `migrationGateFrom(runner)` adapts the runner to the runtime's `MigrationGate`; the runtime's
  `migrating` state consumes `needsMigration()/run()`, and a migration failure maps to `failed` with
  `MIGRATION_FAILED` (verified through `DesktopRuntime` in the suite).

### Tests (`desktop/tests/data/`)

- `app-data-layout.test.ts` — 13 tests: all mutable paths inside userData, normalization, idempotent
  init, denied-write `WRITE_DENIED`, relative-root rejection, root/install overlap (both directions),
  traversal rejection, containPath containment, read-only resource inputs, version-state
  uninitialized/corrupt reads, roundtrip, and atomic commit preserving the prior version.
- `migration-recovery.test.ts` — 14 tests: hash-preserving success + single version advance; files-step
  copy with post-copy verification; no install-root writes; injected step failure keeping old data +
  recoverable backup + typed `STEP_FAILED`; idempotent retry reusing one verified backup/txn;
  interrupted migration resuming from the journal; corrupt backup (`HASH_MISMATCH`/`BACKUP_FAILED`);
  corrupt manifest; explicit `INSUFFICIENT_SPACE` before any write; denied app-data write preserving
  data; bounded retention; restore; plus 2 runtime-wiring tests proving ready is only reached after the
  commit and a failed migration never reports ready (D-43-06).
- `fake-data-fs.ts` — deterministic in-memory `DataFs` with fault injection (deny writes, low disk,
  corrupt reads) and a write log.
- `playwright.config.ts` — self-contained config (mirrors the runtime suite pattern); `testDir` "." so
  `tests/data/*.test.ts` are discoverable without the Electron shell globalSetup.

### Backend config (`backend/app/config.py`)

- Added `storage_dir: str = ""` (env `NOVELMIND_STORAGE_DIR`) under the file-storage section. Empty
  preserves the existing CWD `"storage"` fallback (`getattr(settings, "storage_dir", None) or "storage"`
  in `illustrations/storage.py:207` / `derivative_visual/assets.py:295`); packaged desktop mode
  redirects generation output to `%APPDATA%/NovelMind/data/storage`. Only this item was added on top of
  the user's pre-existing in-tree edits to the file.

### Documentation

- `docs/desktop-data-lifecycle.md` — human-facing data lifecycle policy: layout, version state,
  backup-first transaction, failure/recovery table, data sources and migration order, directory
  ownership, and code pointers.

## Verification (all executed)

1. `cd desktop && npm run typecheck` — PASS for all files owned by this plan (errors remain only in the
   parallel 43-02 executor's in-progress untracked files `process-graph.ts`/`readiness.ts` and their
   test files; out of scope, not touched).
2. `cd desktop && npx playwright test --config tests/data/playwright.config.ts` — **27 passed / 0 failed**
   (13 layout/version-state + 14 migration/recovery + 2 runtime-wiring), ~1s.
3. `cd desktop && npx playwright test --config tests/runtime/playwright.config.ts` — **76 passed / 0 failed**
   (43-01 adapter-contract + state-machine plus the 43-02 executor's in-progress graph/tree suites).
4. `cd backend && venv/Scripts/python.exe -m py_compile app/config.py app/services/illustrations/storage.py app/services/derivative_visual/assets.py` — PASS.
5. `cd backend && venv/Scripts/python.exe -m pytest tests/unit/illustrations/ -q` — **60 passed** (~5s), no storage regression.
6. Config smoke test — `settings.storage_dir` defaults `''` (roots resolve to `storage\illustration_assets` /
   `storage\derivative_asset_candidates`) and `NOVELMIND_STORAGE_DIR` override redirects both roots.
7. `git status --short` — new paths are `desktop/src/data/`, `desktop/tests/data/`,
   `docs/desktop-data-lifecycle.md`, the `storage_dir` addition in `backend/app/config.py`, and
   `.planning/phases/43-*/` artifacts. All pre-existing user modifications (`.gitignore`,
   `.planning/config.json`, `deploy/`, `frontend/next-env.d.ts`, `backend/.env.example`,
   `backend/start-*.bat`, `scripts/*`, and the user's `backend/app/config.py` edits) are untouched.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] FakeDataFs denied-prefix matched every path when unset**
- **Found during:** Task 2/3 test run.
- **Issue:** `denyPathPrefix` defaulted to `null` but the fake's `denyCheck` tested `!== null && startsWith(prefix ?? "")`, so every write matched `startsWith("")` and was denied.
- **Fix:** apply the prefix only when it is a non-empty string (`typeof === "string"`).
- **Files modified:** `desktop/tests/data/fake-data-fs.ts`.

**2. [Rule 1 - Bug] FakeDataFs seed/content keys ignored backslashes, so migration seeds never matched the layout-derived absolute paths**
- **Found during:** Task 2/3 test run.
- **Issue:** the fake tree was keyed by forward-slash paths; Windows-style absolute seed paths produced duplicate nodes and "path not found" errors.
- **Fix:** normalize every seed/read path through `toRel()`; tests seed app-data files under `%APPDATA%/NovelMind/...` so the fake matches the layout root.
- **Files modified:** `desktop/tests/data/fake-data-fs.ts`, `desktop/tests/data/migration-recovery.test.ts`.

**3. [Rule 2 - Correctness] A journal whose backup manifest is unreadable/corrupt must fail typed, not silently re-back-up**
- **Found during:** Task 3 fault-injection review (corrupt-manifest retry).
- **Issue:** `run()` resumed with `null` on an unreadable manifest, falling through to a fresh backup — a corrupt-evidence case that should refuse progress (T-43-03-01).
- **Fix:** distinguish "no journal" (fresh backup) from "journal present but manifest unreadable/corrupt" (typed `BACKUP_FAILED` with recovery instruction).
- **Files modified:** `desktop/src/data/migration-runner.ts`.

### Out-of-scope discoveries (logged, not fixed)

- The parallel 43-02 executor's in-progress `desktop/src/runtime/{process-graph,readiness,port-allocator,process-owner,logging}.ts` and their test files contain transient typecheck errors while that plan is mid-execution; per the scope boundary they were not touched and will be closed by the 43-02 executor.

## Known Stubs

None. Every surface is backed by real behavior; the DB/vector/app-metadata migration steps are injected
by design (the desktop runtime is not a database authority, D-43-04) and are exercised via no-op/failure
stubs in the suites.

## Self-Check: PASSED

- 9 new files created (4 source + 4 test/fixture/config + 1 doc) — verified by `git status --short` and `wc -l`.
- `npm run typecheck` clean for all plan-owned files.
- Data suite 27/27, runtime suite 76/76, backend illustrations 60/60, `py_compile` clean.
- No commits created (orchestrator-owned); no files outside `desktop/src/data/`, `desktop/tests/data/`,
  `docs/desktop-data-lifecycle.md`, the `storage_dir` addition in `backend/app/config.py`, and
  `.planning/phases/43-*/` were touched.
