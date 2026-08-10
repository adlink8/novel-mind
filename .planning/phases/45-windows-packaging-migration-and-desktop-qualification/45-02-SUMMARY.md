---
phase: 45-windows-packaging-migration-and-desktop-qualification
plan: "02"
subsystem: desktop-upgrade-uninstall-recovery
tags: [desktop, upgrade, version-regression, backup-first, rollback, uninstall-preservation, uninstall-policy, checksum-fixture, recovery, update-coordinator]
requires:
  - "45-01 (NSIS per-user installer, deleteAppDataOnUninstall=false, win-unpacked)"
  - "43-03 (versioned %APPDATA%/NovelMind layout + backup-first MigrationRunner)"
  - "43-04 (runtime migration gate + DesktopRuntime state machine)"
provides:
  - "desktop/src/update/upgrade-coordinator.ts — versioned backup-first upgrade transaction (detect/refuse regression, stop owned runtime, verify checksum-pinned fixture, migrate, post-upgrade probe, exact rollback)"
  - "desktop/src/update/uninstall-policy.ts — default uninstall preserves %APPDATA%/NovelMind; explicit data deletion is separate, confirmed and path-contained"
  - "desktop/scripts/create-upgrade-fixture.ps1 — deterministic checksum-pinned prior-version fixture (library/chapters/analysis/visuals/derivatives + resources + migration.json)"
  - "desktop/test-fixtures/prior-version/ — the generated 10-file fixture, schemaVersion 1 / runtime 0.1.0"
  - "desktop/tests/update/ — 23 tests: upgrade-preservation (7), upgrade-recovery (8), uninstall-preservation (8)"
  - "docs/desktop-upgrade-recovery.md — upgrade/failure-recovery/uninstall policy documentation"
affects:
  - "45-03 (clean-VM qualification consumes the fixture + upgrade/recovery specs)"
  - "45-04 (release evidence index: REQ-DESK-09/10 data checks)"
  - "Main-process integration (future plan): wire UpgradeCoordinator + uninstall policy into startup/installer flows"
tech-stack:
  added:
    - "No new npm/PowerShell dependencies — reuse node:fs, node:crypto, playwright, nodeDataFs"
  patterns:
    - "Upgrade as a reversible transaction layered on MigrationRunner (decision -> stop runtime -> verify fixture -> migrate -> probe -> rollback)"
    - "Semver-aware version comparison; schema/runtime regression refused before any write (fail-closed)"
    - "Checksum-pinned prior-version fixture as upgrade evidence (never an empty database)"
    - "Exact restore: restoreBackup + delete extra files + prune empty dirs + restore version metadata + clear journal"
    - "Default uninstall = binaries-only; data deletion as a separate confirmed containPath-bounded action"
    - "Fixture determinism: ASCII-only content, fixed timestamps, UTF-8 no BOM, reproducible manifests"
key-files:
  created:
    - desktop/src/update/upgrade-coordinator.ts
    - desktop/src/update/uninstall-policy.ts
    - desktop/scripts/create-upgrade-fixture.ps1
    - desktop/test-fixtures/prior-version/ (fixture-manifest.json + migration.json + 8 data/resource files)
    - desktop/tests/update/helpers.ts
    - desktop/tests/update/playwright.config.ts
    - desktop/tests/update/upgrade-preservation.spec.ts (7 tests)
    - desktop/tests/update/upgrade-recovery.spec.ts (8 tests)
    - desktop/tests/update/uninstall-preservation.spec.ts (8 tests)
    - docs/desktop-upgrade-recovery.md
  modified:
    - None (no existing source file modified)
decisions:
  - "Upgrade detection compares schema AND runtime version; a newer data/version opened by an old binary is refused (VERSION_REGRESSION) before any write"
  - "Rollback restores the exact pre-upgrade data tree (backup restore + extra-file removal + empty-dir pruning), restores source version metadata and clears the journal so a retry backs up fresh"
  - "Post-upgrade probe failure auto-rolls-back — a failed validation never leaves a committed version"
  - "Default uninstall preserves %APPDATA%/NovelMind (deleteAppDataOnUninstall=false, verified in yml + policy); explicit delete requires confirm and is containPath-bounded"
  - "Fixture generation is deterministic: regenerating produces byte-identical manifests (verified)"
metrics:
  started: "2026-08-11"
  completed: "2026-08-11"
  typecheck: "PASS"
  package_tests: "21 passed (45-01 regression)"
  data_tests: "27 passed (43-03 regression)"
  update_tests: "23 passed twice (7 preservation + 8 recovery + 8 uninstall)"
  fixture: "10 files / 1946 bytes, schema 1 / runtime 0.1.0, regenerated manifests identical"
  uncommitted: true
---

# Phase 45 Plan 02: Upgrade, Failure Recovery and Data-Preserving Uninstall — Summary

## Objective (one-liner)

Qualify the desktop upgrade path: a versioned, backup-first, reversible upgrade
transaction plus a default-data-preserving uninstall policy, proven against a
checksum-pinned prior-version fixture with real NovelMind user data.

## Upgrade Strategy (Task 1 — upgrade-preservation)

`desktop/src/update/upgrade-coordinator.ts` layers upgrade decisions on the
43-03 `MigrationRunner`:

- **Detect** — `evaluateUpgrade` compares the committed version state
  (`migration.json`) with the running binary:
  - schema/runtime version AHEAD of this binary → **regression**, refused
    (`VERSION_REGRESSION`) before any write; data + version metadata untouched;
  - same schema, older runtime version → metadata-only atomic commit;
  - schema below target → full backup-first migration.
- **Prepare** — stop the owned runtime (injected); verify the **checksum-pinned
  prior fixture** per-file SHA-256 against `fixture-manifest.json`; tampered or
  mismatched fixture → `FIXTURE_MISMATCH` refusal with nothing modified
  (T-45-02-01). Backup capacity is the runner's explicit INSUFFICIENT_SPACE gate.
- **Migrate** — declared steps in fixed order; version commit (atomic) only after
  every step succeeded.
- **Validate** — injected post-upgrade domain probe; a probe failure rolls the
  upgrade back (fail-closed: never commits a broken version).
- **Idempotent** — a committed state at/above target returns `current`; no
  re-migration, no second backup; an in-flight journal is resumed from the
  verified backup.

The prior fixture (`desktop/test-fixtures/prior-version/`, generated by
`desktop/scripts/create-upgrade-fixture.ps1`) contains real user data —
library/novels, chapters, analysis, visuals (PNG), derivatives — plus new-version
immutable resources and version metadata (schema 1 / runtime 0.1.0). The ps1 is
deterministic: regenerating produces byte-identical manifests (verified 9 files /
1946 bytes twice).

## Failure Recovery + Rollback (Task 2 — upgrade-recovery)

- Injected step failure: old data stays readable, version never advances, a typed
  `MigrationFailure` carries a bounded recovery instruction, and the backup is
  hash-verifiable (recoverable evidence).
- Retry resumes from the verified backup (same txn, no re-backup) and commits once.
- User-selectable `rollback()` restores the **exact** pre-upgrade state: backup
  restore + removal of files the failed migration added + pruning of empty dirs +
  restore of the source `migration.json` + clearing the journal — a fresh retry
  then starts clean and succeeds.
- Corrupt backup evidence fails the retry typed (`BACKUP_FAILED`) and preserves
  data; insufficient disk space fails explicitly before any byte is written.
- The runtime wired through the migration gate reports `failed` (never `ready`)
  from a failed upgrade; a fixed retry reaches `ready`.

## Uninstall Policy (Task 2/3 — uninstall-preservation)

`desktop/src/update/uninstall-policy.ts`:

- Default uninstall scope removes **binaries only** and preserves
  `%APPDATA%/NovelMind` (electron-builder `deleteAppDataOnUninstall: false`
  re-verified in yml; policy module asserts the same contract).
- Reinstall over preserved data is a hash-identical no-op (upgrade sees `current`).
- Explicit data deletion is a **separate, confirmed, labelled** action:
  - refuses without `confirm: true` (`NOT_CONFIRMED`);
  - target is `containPath`/`isPathInside`-bounded — traversal and outside-root
    absolute paths are refused (`OUTSIDE_APP_DATA`); a confirmed in-root delete
    removes only the requested subtree;
  - a denied delete reports `DELETE_FAILED`, never a false success.

## Verification Evidence

| Check | Result |
|---|---|
| `cd desktop && npm run typecheck` | PASS |
| `npx playwright test --config tests/package/` (45-01 regression) | 21 passed / 0 failed |
| `npx playwright test --config tests/data/` (43-03 regression) | 27 passed / 0 failed |
| `npx playwright test --config tests/update/` | 23 passed / 0 failed — **twice** (PASS 1, PASS 2) |
| Plan Task 1 verify (`upgrade-preservation.spec.ts`) | 7 passed |
| Plan Task 2 verify (`upgrade-recovery.spec.ts` + `uninstall-preservation.spec.ts`) | 16 passed |
| Fixture reproducibility | regenerated manifests byte-identical (9 files / 1946 bytes) |
| `git status` | only new planned files + pre-existing user modifications (none touched) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Rollback left empty directories behind**
- Found during: Task 2 test run.
- Issue: after removing files the failed migration added, empty dirs (e.g.
  `data/templates`) survived, so the restore was not EXACT.
- Fix: added bottom-up `pruneEmptyDirectories` and used `recursive: true` for
  directory `rm` (node fs requires it; plain rm on a dir throws EISDIR, which the
  initial `.catch` silently swallowed).
- Files: `desktop/src/update/upgrade-coordinator.ts`.

**2. [Rule 3 - Blocking] `$PSScriptRoot` unset inside ps1 param default on PowerShell 5.1**
- Found during: Task 1 fixture generation.
- Issue: the default `$OutputDir` expression evaluated `$PSScriptRoot` as empty
  under `powershell -File`, failing `Split-Path`.
- Fix: derive the default in the script body with `$MyInvocation.MyCommand.Path`
  fallback.
- Files: `desktop/scripts/create-upgrade-fixture.ps1`.

**3. [Rule 1 - Bug] `[System.IO.File]::WriteAllBytes` needed parent dirs**
- Found during: Task 1 fixture generation.
- Issue: PNG assets were written before their directories existed.
- Fix: `New-Item -ItemType Directory -Force` before binary writes.
- Files: `desktop/scripts/create-upgrade-fixture.ps1`.

**4. [Rule 1 - Bug] Fixture verifier rejected added files when resuming a partial migration**
- Found during: Task 1/2 test design.
- Issue: after a partial upgrade already copied new resources into `data/`, a
  retry's "no undeclared file" gate would falsely refuse the (legitimate) retry.
- Fix: `createPinnedFixtureVerifier` tolerates undeclared files while
  `ctx.resumingFromJournal` (journal exists); declared entries are still
  hash-checked. Fresh upgrades keep the strict no-undeclared gate.
- Files: `desktop/src/update/upgrade-coordinator.ts`.

## Known Stubs

None. The upgrade/uninstall policies are implemented and qualified. Wiring the
`UpgradeCoordinator` into the Electron main-process startup and the explicit
data-deletion path into the installer/UI is intentionally deferred to a later
plan (no false "ready" claim; the module is a capability to be consumed).

## Threat Flags

None beyond the plan's threat model. The new modules add no network endpoints or
auth paths. The uninstall-policy deletion path is the intended T-45-02-02
mitigation (containPath-bound, confirmed); the coordinator's fixture/backup
checks are the T-45-02-01/T-45-02-03 mitigations.

## Self-Check: PASSED

- `desktop/src/update/upgrade-coordinator.ts`, `uninstall-policy.ts`,
  `desktop/scripts/create-upgrade-fixture.ps1`, `desktop/test-fixtures/prior-version/`
  (10 files), `desktop/tests/update/*` (5 files), `docs/desktop-upgrade-recovery.md` — all exist.
- `npm run typecheck` PASS; package 21/21, data 27/27, update 23/23 (twice) PASS;
  fixture regenerated manifests identical; `git status` shows only planned files
  plus pre-existing user modifications (none touched).
