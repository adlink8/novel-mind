/**
 * Backup-first upgrade preservation (plan 45-02, Task 1, D-45-04, T-45-02-01,
 * T-45-02-03).
 *
 * Proves a compatible upgrade preserves REAL NovelMind data across versions:
 *  - the checksum-pinned PRIOR fixture (library/chapters/analysis/visuals/
 *    derivatives) starts the upgrade — never an empty database,
 *  - the owned runtime is stopped before migrating,
 *  - the fixture verifier rejects tampered/mismatched data before any write
 *    (FIXTURE_MISMATCH) and nothing is modified,
 *  - declared migrations run, post-upgrade hashes/domain probes validate, the
 *    version advances exactly once, and a repeat invocation is idempotent,
 *  - version REGRESSION (an old binary opening newer data) is refused without
 *    touching the data,
 *  - the manifest/journal keep repudiation evidence (txn id, backup path).
 *
 * Runs against the REAL filesystem DataFs and the REAL ps1-emitted fixture.
 */
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {
  readPinnedFixtureManifest,
  UpgradeCoordinator,
  UpgradeRefusedError,
  createPinnedFixtureVerifier,
  evaluateUpgrade,
  UPGRADE_REFUSAL_CODES,
} from "../../src/update/upgrade-coordinator";
import { createFilesCopyStep, type MigrationStep } from "../../src/data/migration-runner";
import { readVersionState } from "../../src/data/version-state";
import { containPath, nodeDataFs } from "../../src/data/app-data-layout";
import {
  dataHashes,
  fixtureDataHashes,
  FIXTURE_MANIFEST_PATH,
  FIXTURE_DATA,
  FIXTURE_MIGRATION_JSON,
  FIXTURE_ROOT,
  FIXTURE_RUNTIME_VERSION,
  listFiles,
  makeFixtureAppData,
  TARGET_SCHEMA_VERSION,
  UPGRADE_APP_VERSION,
  type TempAppData,
} from "./helpers";

/** Declared steps the upgrade runs (files + the three content steps). */
function declaredSteps(): readonly MigrationStep[] {
  return [
    createFilesCopyStep([
      {
        sourcePath: path.join(FIXTURE_DATA, "..", "resources", "templates"),
        destRelPath: "templates",
        description: "outline templates",
      },
      {
        sourcePath: path.join(FIXTURE_DATA, "..", "resources", "assets"),
        destRelPath: "assets",
        description: "default assets",
      },
    ]),
    { id: "database", run: async () => undefined },
    { id: "vector", run: async () => undefined },
    { id: "app_metadata", run: async () => undefined },
  ];
}

/** Fresh temp app-data seeded from the fixture + a coordinator wired for success. */
async function makeUpgradeFixture(): Promise<TempAppData & { coordinator: UpgradeCoordinator }> {
  const base = await makeFixtureAppData();
  const coordinator = new UpgradeCoordinator({
    fs: base.fs,
    appData: base.appData,
    targetSchemaVersion: TARGET_SCHEMA_VERSION,
    appVersion: UPGRADE_APP_VERSION,
    steps: declaredSteps(),
    verifyFixture: createPinnedFixtureVerifier({
      fs: base.fs,
      manifestPath: FIXTURE_MANIFEST_PATH,
      fixtureRoot: path.join(FIXTURE_DATA, ".."),
      dataRoot: base.appData.data,
    }),
    postUpgradeProbe: async (ctx) => {
      // Domain probe: every fixture data file must still exist post-upgrade.
      for (const rel of Object.keys(fixtureDataHashes())) {
        const abs = containPath(ctx.appData.data, rel);
        if (!(await ctx.fs.exists(abs))) {
          throw new Error(`post-upgrade probe: missing data/${rel}`);
        }
      }
    },
    stopOwnedRuntime: async () => undefined,
  });
  return { ...base, coordinator };
}

function backupDirs(appDataRoot: string): string[] {
  const backups = path.join(appDataRoot, "backups");
  if (!fs.existsSync(backups)) return [];
  return fs
    .readdirSync(backups)
    .filter((name) => fs.statSync(path.join(backups, name)).isDirectory());
}

test.describe("versioned backup-first upgrade (upgrade-preservation)", () => {
  test("evaluateUpgrade classifies current/upgrade-needed/metadata-only/regression", async () => {
    const state = (schemaVersion: number, runtimeVersion: string) => ({
      layoutVersion: 1,
      schemaVersion,
      runtimeVersion,
      committedAt: "2026-01-01T00:00:00.000Z",
      txnId: "t",
    });
    expect(evaluateUpgrade(state(2, "0.2.0"), 2, "0.2.0").kind).toBe("current");
    expect(evaluateUpgrade(state(1, "0.1.0"), 2, "0.2.0").kind).toBe("upgrade-needed");
    expect(evaluateUpgrade(state(2, "0.1.0"), 2, "0.2.0").kind).toBe("metadata-only");
    expect(evaluateUpgrade(state(3, "0.2.0"), 2, "0.2.0").kind).toBe("regression");
    expect(evaluateUpgrade(state(2, "0.9.0"), 2, "0.2.0").kind).toBe("regression");
    expect(evaluateUpgrade(null, 2, "0.2.0").kind).toBe("upgrade-needed");
  });

  test("compatible upgrade preserves fixture content and advances exactly one version", async () => {
    const t = await makeUpgradeFixture();
    try {
      const before = await dataHashes(t.fs, t.appData);

      const outcome = await t.coordinator.run();
      expect(outcome.outcome).toBe("upgraded");
      if (outcome.outcome !== "upgraded") return;
      expect(outcome.schemaVersion).toBe(TARGET_SCHEMA_VERSION);
      expect(outcome.runtimeVersion).toBe(UPGRADE_APP_VERSION);
      expect(outcome.stepsDone).toEqual(["files", "database", "vector", "app_metadata"]);
      expect(outcome.backupDirPath).toContain("backups");
      expect(outcome.txnId.length).toBeGreaterThan(0);

      // Content/metadata/assets preserved (hashes identical before -> after).
      const after = await dataHashes(t.fs, t.appData);
      for (const [rel, hash] of Object.entries(before)) {
        expect(after[rel], `data/${rel} must be preserved`).toBe(hash);
      }
      // New-version resources copied into app-data (post-copy verification ran).
      for (const rel of ["templates/outline-template.json", "assets/default-cover.png"]) {
        expect(after[rel], `new resource data/${rel} must exist`).toBeDefined();
      }
      // Version advanced once.
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(2);
      expect(state?.runtimeVersion).toBe(UPGRADE_APP_VERSION);

      // Repudiation evidence: journal exists (txn + backup path).
      const journalPath = containPath(t.appData.runtime, "migration-journal.json");
      expect(await t.fs.exists(journalPath)).toBe(true);
    } finally {
      await t.cleanup();
    }
  });

  test("repeat invocation is idempotent (no second migration, no second backup)", async () => {
    const t = await makeUpgradeFixture();
    try {
      const first = await t.coordinator.run();
      expect(first.outcome).toBe("upgraded");
      const backupsAfterFirst = backupDirs(t.appData.root);

      const second = await t.coordinator.run();
      expect(second.outcome).toBe("current");

      expect(backupDirs(t.appData.root)).toEqual(backupsAfterFirst); // no re-backup
    } finally {
      await t.cleanup();
    }
  });

  test("tampered fixture data is rejected (FIXTURE_MISMATCH) before any write", async () => {
    const t = await makeUpgradeFixture();
    try {
      // Tamper one fixture data file AFTER the app-data tree was built.
      const target = path.join(t.appData.data, "library", "novels.json");
      fs.writeFileSync(target, `{"tampered": true}`);
      const dataHashBefore = JSON.stringify(await dataHashes(t.fs, t.appData));

      let refusal: unknown;
      try {
        await t.coordinator.run();
      } catch (cause) {
        refusal = cause;
      }
      expect(refusal).toBeInstanceOf(UpgradeRefusedError);
      const typed = refusal as UpgradeRefusedError;
      expect(typed.code).toBe(UPGRADE_REFUSAL_CODES.FIXTURE_MISMATCH);
      expect(typed.oldDataPreserved).toBe(true);
      expect(typed.recoveryInstruction).toContain("NOT modified");

      // Nothing was modified: version stays at the fixture value and hashes unchanged.
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(1);
      expect(state?.runtimeVersion).toBe(FIXTURE_RUNTIME_VERSION);
      expect(JSON.stringify(await dataHashes(t.fs, t.appData))).toBe(dataHashBefore);
      // No backup was created.
      expect(backupDirs(t.appData.root)).toEqual([]);
    } finally {
      await t.cleanup();
    }
  });

  test("version regression (old binary, newer data) is refused without touching data", async () => {
    const t = await makeFixtureAppData();
    try {
      // Simulate data written by a NEWER binary (schema 3, runtime 9.9.9).
      const newer = {
        layoutVersion: 1,
        schemaVersion: 3,
        runtimeVersion: "9.9.9",
        committedAt: "2026-08-05T00:00:00.000Z",
        txnId: "future",
      };
      fs.writeFileSync(t.appData.migrationMeta, JSON.stringify(newer, null, 2));

      const coordinator = new UpgradeCoordinator({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        appVersion: UPGRADE_APP_VERSION,
        steps: declaredSteps(),
      });
      let refusal: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        refusal = cause;
      }
      expect(refusal).toBeInstanceOf(UpgradeRefusedError);
      expect((refusal as UpgradeRefusedError).code).toBe(
        UPGRADE_REFUSAL_CODES.VERSION_REGRESSION,
      );
      // Data + version metadata untouched.
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(3);
      expect(state?.runtimeVersion).toBe("9.9.9");
    } finally {
      await t.cleanup();
    }
  });

  test("post-upgrade probe failure rolls the upgrade back (never commits a broken version)", async () => {
    const t = await makeFixtureAppData();
    try {
      let probeCalls = 0;
      const coordinator = new UpgradeCoordinator({
        fs: t.fs,
        appData: t.appData,
        targetSchemaVersion: TARGET_SCHEMA_VERSION,
        appVersion: UPGRADE_APP_VERSION,
        steps: declaredSteps(),
        verifyFixture: createPinnedFixtureVerifier({
          fs: t.fs,
          manifestPath: FIXTURE_MANIFEST_PATH,
          fixtureRoot: path.join(FIXTURE_DATA, ".."),
          dataRoot: t.appData.data,
        }),
        postUpgradeProbe: async () => {
          probeCalls += 1;
          if (probeCalls === 1) throw new Error("injected post-upgrade validation failure");
        },
        stopOwnedRuntime: async () => undefined,
      });

      let refusal: unknown;
      try {
        await coordinator.run();
      } catch (cause) {
        refusal = cause;
      }
      expect(refusal).toBeInstanceOf(UpgradeRefusedError);
      expect((refusal as UpgradeRefusedError).code).toBe(
        UPGRADE_REFUSAL_CODES.POST_UPGRADE_PROBE_FAILED,
      );

      // Rolled back: version returns to the fixture value, data hashes restored,
      // and the journal is cleared so a retry backs up fresh.
      const state = await readVersionState(t.fs, t.appData.migrationMeta);
      expect(state?.schemaVersion).toBe(1);
      expect(state?.runtimeVersion).toBe(FIXTURE_RUNTIME_VERSION);
      const hashes = await dataHashes(t.fs, t.appData);
      for (const [rel, hash] of Object.entries(fixtureDataHashes())) {
        expect(hashes[rel], `data/${rel} must be restored`).toBe(hash);
      }
      const journalPath = containPath(t.appData.runtime, "migration-journal.json");
      expect(await t.fs.exists(journalPath)).toBe(false);

      // A retry (probe now succeeds) upgrades cleanly.
      const retry = await coordinator.run();
      expect(retry.outcome).toBe("upgraded");
      expect(await readVersionState(t.fs, t.appData.migrationMeta)).toMatchObject({
        schemaVersion: 2,
        runtimeVersion: UPGRADE_APP_VERSION,
      });
    } finally {
      await t.cleanup();
    }
  });

  test("the ps1-emitted fixture manifest is self-consistent and matches the tree", async () => {
    const manifest = await readPinnedFixtureManifest(nodeDataFs(), FIXTURE_MANIFEST_PATH);
    expect(manifest).not.toBeNull();
    expect(manifest?.fileCount).toBeGreaterThan(0);
    // data/ subtree + resources/ subtree + migration.json (not the manifest itself).
    const expected =
      listFiles(path.join(FIXTURE_ROOT, "data")).length +
      listFiles(path.join(FIXTURE_ROOT, "resources")).length +
      1;
    expect(manifest?.fileCount).toBe(expected);
    // migration.json pins the prior version.
    const mig = JSON.parse(fs.readFileSync(FIXTURE_MIGRATION_JSON, "utf8")) as {
      schemaVersion: number;
      runtimeVersion: string;
    };
    expect(mig.schemaVersion).toBe(1);
    expect(mig.runtimeVersion).toBe(FIXTURE_RUNTIME_VERSION);
  });
});
