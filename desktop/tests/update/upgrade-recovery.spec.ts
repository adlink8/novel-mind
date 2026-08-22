/**
 * Failed-upgrade recovery and rollback (plan 45-02, Task 2, D-45-04,
 * T-45-02-01/T-45-02-03).
 *
 * Proves a failed upgrade is reversible and never loses user work:
 *  - an injected migration step failure keeps the OLD data readable, the typed
 *    `MigrationFailure` carries a bounded recovery instruction and the version
 *    is NOT advanced (the runtime never reports ready from a partial migration),
 *  - a retry resumes from the verified backup (idempotent — no second backup),
 *  - a partial migration (files already copied) that then fails can be rolled
 *    back by the user to the EXACT pre-upgrade data + version,
 *  - corruption of the backup evidence fails the retry typed and preserves data,
 *  - insufficient disk space fails EXPLICITLY before any byte is written,
 *  - the runtime wired through the migration gate reports `failed` — not
 *    `ready` — when the upgrade cannot commit.
 *
 * Runs against the REAL filesystem DataFs and the ps1-emitted prior fixture
 * (real user data, not an empty database).
 */
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {
  UpgradeCoordinator,
  createPinnedFixtureVerifier,
  UPGRADE_REFUSAL_CODES,
} from "../../src/update/upgrade-coordinator";
import {
  MigrationFailure,
  MigrationRunner,
  migrationGateFrom,
  type MigrationStep,
} from "../../src/data/migration-runner";
import { readVersionState } from "../../src/data/version-state";
import { readBackupManifest, verifyBackup } from "../../src/data/backup";
import { containPath } from "../../src/data/app-data-layout";
import { RUNTIME_ERROR_CODES } from "../../src/runtime/types";
import { DesktopRuntime } from "../../src/runtime/desktop-runtime";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import { createFakeOps } from "../runtime/fake-process-ops";
import {
  dataHashes,
  fixtureDataHashes,
  FIXTURE_MANIFEST_PATH,
  FIXTURE_DATA,
  FIXTURE_RUNTIME_VERSION,
  makeFixtureAppData,
  TARGET_SCHEMA_VERSION,
  UPGRADE_APP_VERSION,
} from "./helpers";

function resources(): readonly MigrationStep[] {
  return [
    {
      id: "files",
      run: async (ctx) => {
        // Simulate a declared files step that copies immutable resources.
        const src = path.join(FIXTURE_DATA, "..", "resources", "templates");
        const destRel = "templates";
        for (const entry of fs.readdirSync(src)) {
          const destAbs = containPath(ctx.appData.data, destRel, entry);
          await ctx.fs.mkdir(containPath(ctx.appData.data, destRel), { recursive: true });
          await ctx.fs.copyFile(path.join(src, entry), destAbs);
        }
      },
    },
  ];
}

test.describe("failed-upgrade recovery (upgrade-recovery)", () => {
  test("a failed step keeps old data readable, never advances the version, and carries a typed recovery instruction", async () => {
    const t = await makeFixtureAppData();
    try {
      let databaseCalls = 0;
      const coordinator = new UpgradeCoordinator({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        appVersion: UPGRADE_APP_VERSION,
        steps: [
          ...resources(),
          {
            id: "database",
            run: async () => {
              databaseCalls += 1;
              if (databaseCalls === 1) throw new Error("database step blew up (injected)");
            },
          },
          { id: "vector", run: async () => undefined },
          { id: "app_metadata", run: async () => undefined },
        ],
        verifyFixture: createPinnedFixtureVerifier({
          fs: t.fs,
          manifestPath: FIXTURE_MANIFEST_PATH,
          fixtureRoot: path.join(FIXTURE_DATA, ".."),
          dataRoot: t.appData.data,
        }),
        stopOwnedRuntime: async () => undefined,
      });

      let failure: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        failure = cause;
      }
      expect(failure).toBeInstanceOf(MigrationFailure);
      const typed = failure as MigrationFailure;
      expect(typed.code).toBe("STEP_FAILED");
      expect(typed.step).toBe("database");
      expect(typed.oldDataPreserved).toBe(true);
      expect(typed.backupDirPath).not.toBeNull();
      expect(typed.recoveryInstruction).toContain("backup");
      expect(typed.recoveryInstruction).toContain("retry");

      // Old data intact + readable (hashes match the fixture exactly).
      const hashes = await dataHashes(t.fs, t.appData);
      for (const [rel, hash] of Object.entries(fixtureDataHashes())) {
        expect(hashes[rel], `data/${rel} must be preserved`).toBe(hash);
      }
      // Version NOT advanced — the runtime can never report ready from this.
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(1);
      expect(state?.runtimeVersion).toBe(FIXTURE_RUNTIME_VERSION);

      // Backup is preserved and hash-verified (recoverable evidence).
      expect(typed.backupDirPath).not.toBeNull();
      const manifest = await readBackupManifest(
        t.fs,
        containPath(typed.backupDirPath as string, "manifest.json"),
      );
      expect(manifest).not.toBeNull();
      await expect(
        verifyBackup(t.fs, manifest as never, typed.backupDirPath as string),
      ).resolves.toBeUndefined();
    } finally {
      await t.cleanup();
    }
  });

  test("a retry resumes from the verified backup and commits once (idempotent)", async () => {
    const t = await makeFixtureAppData();
    try {
      let databaseCalls = 0;
      const coordinator = new UpgradeCoordinator({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        appVersion: UPGRADE_APP_VERSION,
        steps: [
          ...resources(),
          {
            id: "database",
            run: async () => {
              databaseCalls += 1;
              if (databaseCalls === 1) throw new Error("database step blew up (injected)");
            },
          },
          { id: "vector", run: async () => undefined },
          { id: "app_metadata", run: async () => undefined },
        ],
        verifyFixture: createPinnedFixtureVerifier({
          fs: t.fs,
          manifestPath: FIXTURE_MANIFEST_PATH,
          fixtureRoot: path.join(FIXTURE_DATA, ".."),
          dataRoot: t.appData.data,
        }),
        stopOwnedRuntime: async () => undefined,
      });

      let first: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        first = cause;
      }
      const firstFailure = first as MigrationFailure;
      const backupDirsBeforeRetry = fs
        .readdirSync(path.join(t.appData.root, "backups"))
        .filter((name) =>
          fs.statSync(path.join(t.appData.root, "backups", name)).isDirectory(),
        );

      // Retry succeeds and reuses the same verified backup (no re-backup).
      const outcome = await coordinator.run();
      expect(outcome.outcome).toBe("upgraded");
      if (outcome.outcome !== "upgraded") return;
      expect(outcome.txnId).toBe(firstFailure.txnId);
      expect(outcome.stepsDone).toEqual(["files", "database", "vector", "app_metadata"]);
      const backupDirsAfterRetry = fs
        .readdirSync(path.join(t.appData.root, "backups"))
        .filter((name) =>
          fs.statSync(path.join(t.appData.root, "backups", name)).isDirectory(),
        );
      expect(backupDirsAfterRetry).toEqual(backupDirsBeforeRetry);
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(2);
      expect(state?.runtimeVersion).toBe(UPGRADE_APP_VERSION);
    } finally {
      await t.cleanup();
    }
  });

  test("user rollback restores the EXACT pre-upgrade data and version", async () => {
    const t = await makeFixtureAppData();
    try {
      let databaseCalls = 0;
      const coordinator = new UpgradeCoordinator({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        appVersion: UPGRADE_APP_VERSION,
        steps: [
          ...resources(), // files step ADDS templates/ into data/ before it fails
          {
            id: "database",
            run: async () => {
              databaseCalls += 1;
              if (databaseCalls === 1) throw new Error("database step blew up (injected)");
            },
          },
        ],
        verifyFixture: createPinnedFixtureVerifier({
          fs: t.fs,
          manifestPath: FIXTURE_MANIFEST_PATH,
          fixtureRoot: path.join(FIXTURE_DATA, ".."),
          dataRoot: t.appData.data,
        }),
        stopOwnedRuntime: async () => undefined,
      });

      let failure: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        failure = cause;
      }
      expect(failure).toBeInstanceOf(MigrationFailure);
      // A partial migration has run: the new templates now live in data/.
      expect(fs.existsSync(path.join(t.appData.data, "templates", "outline-template.json"))).toBe(
        true,
      );

      const rollback = await coordinator.rollback();
      expect(rollback.ok).toBe(true);
      if (!rollback.ok) return;
      expect(rollback.sourceSchemaVersion).toBe(1);

      // Exactly the pre-upgrade data: original hashes back, added files gone.
      const hashes = await dataHashes(t.fs, t.appData);
      const fixture = fixtureDataHashes();
      expect(Object.keys(hashes).sort()).toEqual(Object.keys(fixture).sort());
      for (const [rel, hash] of Object.entries(fixture)) {
        expect(hashes[rel]).toBe(hash);
      }
      expect(fs.existsSync(path.join(t.appData.data, "templates"))).toBe(false);

      // Version state restored to the pre-upgrade value and the journal cleared,
      // so a FRESH upgrade starts clean and succeeds.
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(1);
      expect(state?.runtimeVersion).toBe(FIXTURE_RUNTIME_VERSION);
      expect(fs.existsSync(containPath(t.appData.runtime, "migration-journal.json"))).toBe(false);

      const fresh = await coordinator.run();
      expect(fresh.outcome).toBe("upgraded");
      expect(await readVersionState(t.fs, t.appData.migrationMeta)).toMatchObject({
        schemaVersion: 2,
      });
    } finally {
      await t.cleanup();
    }
  });

  test("corrupt backup evidence fails the retry typed and preserves the old data", async () => {
    const t = await makeFixtureAppData();
    try {
      const coordinator = new UpgradeCoordinator({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        appVersion: UPGRADE_APP_VERSION,
        steps: [
          ...resources(),
          {
            id: "database",
            run: async () => {
              throw new Error("database step blew up (injected)");
            },
          },
        ],
        verifyFixture: createPinnedFixtureVerifier({
          fs: t.fs,
          manifestPath: FIXTURE_MANIFEST_PATH,
          fixtureRoot: path.join(FIXTURE_DATA, ".."),
          dataRoot: t.appData.data,
        }),
        stopOwnedRuntime: async () => undefined,
      });

      let failure: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        failure = cause;
      }
      const typed = failure as MigrationFailure;
      // Corrupt a backed-up file so the retry cannot reuse the evidence.
      const backupDir = typed.backupDirPath as string;
      const target = containPath(backupDir, "data/library/novels.json");
      fs.writeFileSync(target, "CORRUPTED");

      let retryFailure: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        retryFailure = cause;
      }
      expect(retryFailure).toBeInstanceOf(MigrationFailure);
      expect((retryFailure as MigrationFailure).code).toBe("BACKUP_FAILED");
      // Old data intact.
      const hashes = await dataHashes(t.fs, t.appData);
      for (const [rel, hash] of Object.entries(fixtureDataHashes())) {
        expect(hashes[rel], `data/${rel} must be preserved`).toBe(hash);
      }
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(1);
    } finally {
      await t.cleanup();
    }
  });

  test("insufficient disk space fails explicitly before any byte is written", async () => {
    const t = await makeFixtureAppData();
    try {
      // Patch the real DataFs' statFreeBytes to report an exhausted volume.
      const fsAny = t.fs as { statFreeBytes: (p: string) => Promise<number> };
      fsAny.statFreeBytes = async () => 1; // 1 free byte for a ~1.9KB fixture
      const coordinator = new UpgradeCoordinator({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        appVersion: UPGRADE_APP_VERSION,
        steps: [...resources()],
        verifyFixture: createPinnedFixtureVerifier({
          fs: t.fs,
          manifestPath: FIXTURE_MANIFEST_PATH,
          fixtureRoot: path.join(FIXTURE_DATA, ".."),
          dataRoot: t.appData.data,
        }),
        stopOwnedRuntime: async () => undefined,
      });

      let failure: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        failure = cause;
      }
      expect(failure).toBeInstanceOf(MigrationFailure);
      expect((failure as MigrationFailure).code).toBe("INSUFFICIENT_SPACE");
      // Nothing written: no backup dir, no journal, version unchanged.
      const backups = path.join(t.appData.root, "backups");
      const backupDirs = fs.existsSync(backups)
        ? fs.readdirSync(backups).filter((n) => fs.statSync(path.join(backups, n)).isDirectory())
        : [];
      expect(backupDirs).toEqual([]);
      expect(fs.existsSync(containPath(t.appData.runtime, "migration-journal.json"))).toBe(false);
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(1);
    } finally {
      await t.cleanup();
    }
  });

  test("a failed upgrade never reports runtime ready (migration gate wiring)", async () => {
    const t = await makeFixtureAppData();
    try {
      const runner = new MigrationRunner({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        steps: [
          ...resources(),
          {
            id: "database",
            run: async () => {
              throw new Error("database step blew up (injected)");
            },
          },
          { id: "vector", run: async () => undefined },
          { id: "app_metadata", run: async () => undefined },
        ],
        appVersion: UPGRADE_APP_VERSION,
      });
      const gate = migrationGateFrom(runner);
      const runtime = new DesktopRuntime({
        adapter: new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
          repoRoot: "C:/fake-repo",
        }),
        migration: gate,
      });

      const snapshot = await runtime.ensureReady();
      expect(snapshot.ready).toBe(false);
      expect(snapshot.state).toBe("failed");
      expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);
      expect(await runner.needsMigration()).toBe(true); // old version preserved
      // Old data remains readable.
      const hashes = await dataHashes(t.fs, t.appData);
      for (const [rel, hash] of Object.entries(fixtureDataHashes())) {
        expect(hashes[rel], `data/${rel} must be preserved`).toBe(hash);
      }

      // Fix the failure: a retry reaches ready and commits once.
      const retryRunner = new MigrationRunner({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        steps: [...resources(), { id: "database", run: async () => undefined }],
        appVersion: UPGRADE_APP_VERSION,
      });
      const retryRuntime = new DesktopRuntime({
        adapter: new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
          repoRoot: "C:/fake-repo",
        }),
        migration: migrationGateFrom(retryRunner),
      });
      const recovered = await retryRuntime.ensureReady();
      expect(recovered.state).toBe("ready");
      expect(await retryRunner.needsMigration()).toBe(false);
    } finally {
      await t.cleanup();
    }
  });

  test("refusing a regression reports an explicit recovery instruction", async () => {
    const t = await makeFixtureAppData();
    try {
      fs.writeFileSync(
        t.appData.migrationMeta,
        JSON.stringify({
          layoutVersion: 1,
          schemaVersion: 5,
          runtimeVersion: "5.0.0",
          committedAt: "2026-08-06T00:00:00.000Z",
          txnId: "future-write",
        }),
      );
      const coordinator = new UpgradeCoordinator({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        appVersion: UPGRADE_APP_VERSION,
        steps: [...resources()],
      });
      let refusal: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        refusal = cause;
      }
      const typed = refusal as { code: string; recoveryInstruction: string; oldDataPreserved: true };
      expect(typed.code).toBe(UPGRADE_REFUSAL_CODES.VERSION_REGRESSION);
      expect(typed.oldDataPreserved).toBe(true);
      expect(typed.recoveryInstruction).toMatch(/NOT modified/);
      // No backup was created for a refused regression.
      const backups = path.join(t.appData.root, "backups");
      const backupDirs = fs.existsSync(backups)
        ? fs.readdirSync(backups).filter((n) => fs.statSync(path.join(backups, n)).isDirectory())
        : [];
      expect(backupDirs).toEqual([]);
    } finally {
      await t.cleanup();
    }
  });
});

const BUDGETS = { startTimeoutMs: 300, drainMs: 50, killMs: 50 };
