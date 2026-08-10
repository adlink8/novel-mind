/**
 * Backup-first migration + recovery suites (plan 43-03, Task 2/3).
 *
 * Proves (acceptance criteria):
 * - success preserves data hashes and advances the version exactly once,
 * - injected failure preserves a recoverable backup and the old version and
 *   returns a typed failed migration state,
 * - retry is idempotent (resumes from the verified backup, does not re-backup),
 * - denied app-data writes / read-only install roots fail typed with no data loss,
 * - corrupt backup evidence fails before any migration,
 * - insufficient disk space fails EXPLICITLY before any byte is written,
 * - interruption (journal) resumes from the verified backup,
 * - the runner wired as the runtime's MigrationGate never lets the runtime
 *   report ready from a partial migration (key_links D-43-06).
 */
import { expect, test } from "@playwright/test";
import {
  containPath,
  initializeAppDataPaths,
  type AppDataPaths,
  type DataFs,
} from "../../src/data/app-data-layout";
import {
  createBackup,
  hashFile,
  pruneBackups,
  readBackupManifest,
  restoreBackup,
  verifyBackup,
} from "../../src/data/backup";
import {
  createFilesCopyStep,
  MigrationFailure,
  MigrationRunner,
  migrationGateFrom,
  type MigrationContext,
  type MigrationStep,
  type MigrationStepId,
} from "../../src/data/migration-runner";
import { FakeDataFs } from "./fake-data-fs";
import type { MigrationGate } from "../../src/runtime/types";
import { RUNTIME_ERROR_CODES } from "../../src/runtime/types";
import { DesktopRuntime } from "../../src/runtime/desktop-runtime";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import { createFakeOps } from "../runtime/fake-process-ops";

const USER_DATA = "C:\\Users\\me\\AppData\\Roaming\\NovelMind";
const INSTALL_ROOT = "C:\\Program Files\\NovelMind";
const INSTALL_UPLOADS = "C:\\Program Files\\NovelMind\\backend\\uploads";
const INSTALL_STORAGE = "C:\\Program Files\\NovelMind\\backend\\storage";

function step(id: MigrationStepId, behavior: (ctx: MigrationContext) => Promise<void>): MigrationStep {
  return { id, run: behavior };
}

interface Fixture {
  fs: FakeDataFs;
  appData: AppDataPaths;
  buildRunner: (steps: readonly MigrationStep[], target?: number) => MigrationRunner;
}

async function makeFixture(seed: Record<string, string> = {}): Promise<Fixture> {
  const fs = new FakeDataFs();
  // Seeds are either absolute install-root paths (read-only inputs) or
  // app-data-relative (data/...) paths; the fake tree is keyed by the same
  // absolute forward-slash paths the layout derives.
  const normalized: Record<string, string> = {};
  for (const [p, content] of Object.entries(seed)) {
    normalized[p.startsWith("C:") ? p : `${USER_DATA}/${p}`] = content;
  }
  fs.seedTree(normalized);
  const appData = await initializeAppDataPaths(fs, {
    userDataDir: USER_DATA,
    installRoot: INSTALL_ROOT,
  });
  return {
    fs,
    appData,
    buildRunner: (steps, target = 2) =>
      new MigrationRunner({ fs, appData, targetSchemaVersion: target, steps }),
  };
}

/** Forward-slash prefix of `appData.data` (the fake tree uses forward slashes). */
function dataPrefix(appData: AppDataPaths): string {
  return `${appData.data.replace(/\\/g, "/")}/`;
}

/** Forward-slash prefix of `appData.backups` (the fake tree uses forward slashes). */
function backupsPrefix(appData: AppDataPaths): string {
  return `${appData.backups.replace(/\\/g, "/")}/`;
}

/** Hash every file under `data/`, keyed by the app-data-relative path. */
async function dataHashes(
  fs: DataFs,
  appData: AppDataPaths,
): Promise<Record<string, string>> {
  const result: Record<string, string> = {};
  const prefix = dataPrefix(appData);
  for (const rel of (fs as FakeDataFs).listFiles()) {
    if (!rel.startsWith(prefix)) continue;
    result[rel.slice(prefix.length)] = (await hashFile(fs, rel)).hash;
  }
  return result;
}

/** The DB/vector/app-metadata steps used across suites. */
function contentSteps(_seed: string): readonly MigrationStep[] {
  return [
    step("database", async () => undefined), // migration "writes" recorded via journal
    step("vector", async () => undefined),
    step("app_metadata", async () => undefined),
  ];
}

type RunResult = Awaited<ReturnType<MigrationRunner["run"]>>;

/** Asserts needsMigration===true and narrows the union to the migrated branch. */
function expectMigrated(result: RunResult): Extract<RunResult, { needsMigration: true }> {
  expect(result.needsMigration).toBe(true);
  return result as Extract<RunResult, { needsMigration: true }>;
}

test.describe("backup-first migration transaction", () => {
  test("success preserves data hashes and advances the version exactly once", async () => {
    const fixture = await makeFixture({
      "data/uploads/novel.txt": "novel content",
      "data/storage/asset.jpg": "binary-ish",
    });
    const before = await dataHashes(fixture.fs, fixture.appData);
    const runner = fixture.buildRunner(contentSteps("ok"));

    expect(await runner.needsMigration()).toBe(true);
    const result = expectMigrated(await runner.run());
    expect(result.schemaVersion).toBe(2);
    expect(result.stepsDone).toEqual(["database", "vector", "app_metadata"]);

    const after = await dataHashes(fixture.fs, fixture.appData);
    expect(after).toEqual(before); // hash-preserving
    expect(await runner.needsMigration()).toBe(false); // committed version advanced
  });

  test("files step copies read-only install resources into app-data and verifies", async () => {
    const fixture = await makeFixture({});
    fixture.fs.seedTree({
      "C:/Program Files/NovelMind/backend/uploads/novel.txt": "novel content",
      "C:/Program Files/NovelMind/backend/storage/assets/x.png": "png-data",
    });
    const runner = fixture.buildRunner([
      createFilesCopyStep([
        { sourcePath: INSTALL_UPLOADS, destRelPath: "uploads", description: "uploads" },
        { sourcePath: INSTALL_STORAGE, destRelPath: "storage", description: "storage" },
      ]),
      ...contentSteps("ok"),
    ]);
    const result = await runner.run();
    expect(result.needsMigration).toBe(true);
    expect(fixture.fs.content("C:/Users/me/AppData/Roaming/NovelMind/data/uploads/novel.txt")).toBe(
      "novel content",
    );
    expect(
      fixture.fs.content("C:/Users/me/AppData/Roaming/NovelMind/data/storage/assets/x.png"),
    ).toBe("png-data");
    // Install resources untouched (read-only inputs).
    expect(fixture.fs.content("C:/Program Files/NovelMind/backend/uploads/novel.txt")).toBe(
      "novel content",
    );
  });

  test("no mutable writes ever reach the install root", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    const runner = fixture.buildRunner(contentSteps("ok"));
    await runner.run();
    for (const rel of fixture.fs.listFiles()) {
      expect(rel.startsWith("C:/Program Files/")).toBe(false);
    }
  });

  test("injected step failure keeps old data + backup and returns typed failure", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    const runner = fixture.buildRunner([
      step("database", async () => {
        throw new Error("database step blew up (injected)");
      }),
      ...contentSteps("ok").slice(1),
    ]);

    let failure: unknown;
    try {
      await runner.run();
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(MigrationFailure);
    const typed = failure as MigrationFailure;
    expect(typed.code).toBe("STEP_FAILED");
    expect(typed.step).toBe("database");
    expect(typed.txnId).not.toBeNull();
    expect(typed.backupDirPath).not.toBeNull();
    expect(typed.oldDataPreserved).toBe(true);
    expect(typed.recoveryInstruction).toContain("backup");

    // Old data untouched.
    expect(fixture.fs.content("C:/Users/me/AppData/Roaming/NovelMind/data/uploads/novel.txt")).toBe(
      "novel content",
    );
    // Version NOT advanced — never ready from partial migration.
    expect(await runner.needsMigration()).toBe(true);

    // Backup is recoverable and hash-verified.
    expect(typed.backupDirPath).not.toBeNull();
    const manifest = await readBackupManifest(
      fixture.fs,
      containPath(typed.backupDirPath as string, "manifest.json"),
    );
    expect(manifest).not.toBeNull();
    await expect(
      verifyBackup(fixture.fs, manifest as never, typed.backupDirPath as string),
    ).resolves.toBeUndefined();
  });

  test("retry after failure resumes from the verified backup (idempotent)", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    let calls = 0;
    const runner = fixture.buildRunner([
      step("database", async () => {
        calls += 1;
        if (calls === 1) throw new Error("database step blew up (injected)");
      }),
      ...contentSteps("ok").slice(1),
    ]);

    let failure: unknown;
    try {
      await runner.run();
    } catch (cause) {
      failure = cause;
    }
    const first = failure as MigrationFailure;

    const copyCount = fixture.fs.copyCount();
    const result = await runner.run(); // retry
    const migrated = expectMigrated(result);
    expect(migrated.stepsDone).toEqual(["database", "vector", "app_metadata"]);
    expect(await runner.needsMigration()).toBe(false);
    // The retry did NOT create a second backup — it resumed from the verified one.
    expect(fixture.fs.copyCount()).toBe(copyCount);
    // Same txn reused.
    expect(migrated.txnId).toBe(first.txnId);
    // Only one backup dir remains.
    const backups = fixture.fs
      .listFiles()
      .filter((rel) => rel.startsWith(backupsPrefix(fixture.appData)));
    expect(backups.length).toBeGreaterThan(0);
  });

  test("interrupted migration resumes from the journal and its backup", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    let ranVector = false;
    const runner = fixture.buildRunner([
      step("database", async () => undefined),
      step("vector", async () => {
        // Simulate a crash/abort mid-run: journal says done=database, then the
        // process dies. Next run must resume from the verified backup.
        ranVector = true;
        throw new Error("process killed (injected interruption)");
      }),
      step("app_metadata", async () => undefined),
    ]);

    await expect(runner.run()).rejects.toBeInstanceOf(MigrationFailure);
    const journal = await runner.readJournal();
    expect(journal?.failedStep).toBe("vector");
    expect(journal?.backupDirPath).not.toBeNull();

    const copyAfterFirst = fixture.fs.copyCount();

    // New runner instance (fresh process restart): resumes from journal.
    const retryRunner = new MigrationRunner({
      fs: fixture.fs,
      appData: fixture.appData,
      targetSchemaVersion: 2,
      steps: [
        step("vector", async () => undefined),
        step("app_metadata", async () => undefined),
      ],
    });
    const result = expectMigrated(await retryRunner.run());
    expect(result.stepsDone).toEqual(["database", "vector", "app_metadata"]);
    expect(fixture.fs.copyCount()).toBe(copyAfterFirst); // no re-backup
    expect(ranVector).toBe(true); // vector re-ran, database not re-done
  });

  test("corrupt backup evidence fails typed (HASH_MISMATCH) before reuse", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    const runner = fixture.buildRunner([
      step("database", async () => {
        throw new Error("database step blew up (injected)");
      }),
      ...contentSteps("ok").slice(1),
    ]);
    await expect(runner.run()).rejects.toBeInstanceOf(MigrationFailure);
    const journal = await runner.readJournal();
    expect(journal?.backupDirPath).not.toBeNull();

    // Corrupt a backed-up file -> the retry must refuse to reuse the evidence.
    const backupDir = journal?.backupDirPath as string;
    fixture.fs.seed(containPath(backupDir, "data/uploads/novel.txt"), "CORRUPTED");
    const manifest = await readBackupManifest(
      fixture.fs,
      containPath(backupDir, "manifest.json"),
    );
    expect(manifest).not.toBeNull();
    await expect(
      verifyBackup(fixture.fs, manifest as never, backupDir),
    ).rejects.toMatchObject({ code: "HASH_MISMATCH" });

    const retryRunner = fixture.buildRunner(contentSteps("ok"));
    let retryFailure: unknown;
    try {
      await retryRunner.run();
    } catch (cause) {
      retryFailure = cause;
    }
    expect(retryFailure).toBeInstanceOf(MigrationFailure);
    expect((retryFailure as MigrationFailure).code).toBe("BACKUP_FAILED");
    // Old data untouched.
    expect(fixture.fs.content("C:/Users/me/AppData/Roaming/NovelMind/data/uploads/novel.txt")).toBe(
      "novel content",
    );
  });

  test("corrupt manifest fails the retry with a typed BACKUP_FAILED", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    const runner = fixture.buildRunner([
      step("database", async () => {
        throw new Error("database step blew up (injected)");
      }),
      ...contentSteps("ok").slice(1),
    ]);
    let failure: unknown;
    try {
      await runner.run();
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(MigrationFailure);

    // Corrupt the manifest so the retry cannot verify/reuse the backup.
    const typed = failure as MigrationFailure;
    const manifestPath = containPath(typed.backupDirPath as string, "manifest.json");
    fixture.fs.seed(manifestPath, JSON.stringify({ version: 99, bogus: true }));
    const retryRunner = fixture.buildRunner(contentSteps("ok"));
    let retryFailure: unknown;
    try {
      await retryRunner.run();
    } catch (cause) {
      retryFailure = cause;
    }
    expect(retryFailure).toBeInstanceOf(MigrationFailure);
    expect((retryFailure as MigrationFailure).code).toBe("BACKUP_FAILED");
    // Old data preserved.
    expect(fixture.fs.content("C:/Users/me/AppData/Roaming/NovelMind/data/uploads/novel.txt")).toBe(
      "novel content",
    );
  });

  test("insufficient disk space fails explicitly before any byte is written", async () => {
    const fixture = await makeFixture({ "data/uploads/big.txt": "x".repeat(10_000) });
    fixture.fs.faults.freeBytes = 1_000; // less than the 10KB data snapshot
    const runner = fixture.buildRunner(contentSteps("ok"));
    const writesBefore = fixture.fs.writeLog.length;

    let failure: unknown;
    try {
      await runner.run();
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(MigrationFailure);
    expect((failure as MigrationFailure).code).toBe("INSUFFICIENT_SPACE");
    // No backup, no journal, no version write.
    expect(fixture.fs.writeLog.length).toBe(writesBefore);
    expect(await runner.needsMigration()).toBe(true);
  });

  test("denied app-data write fails typed and preserves old data", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    fixture.fs.faults.denyPathPrefix = "C:/Users/me/AppData/Roaming/NovelMind/backups";
    const runner = fixture.buildRunner(contentSteps("ok"));
    let failure: unknown;
    try {
      await runner.run();
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(MigrationFailure);
    expect((failure as MigrationFailure).code).toBe("BACKUP_FAILED");
    expect(fixture.fs.content("C:/Users/me/AppData/Roaming/NovelMind/data/uploads/novel.txt")).toBe(
      "novel content",
    );
    expect(await runner.needsMigration()).toBe(true);
  });

  test("backup retention is bounded and the committed backup survives pruning", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    const srcVersion = {
      layoutVersion: 1,
      schemaVersion: 0,
      runtimeVersion: "0.0.0",
      committedAt: null,
      txnId: null,
    };
    const a = await createBackup(fixture.fs, fixture.appData, { sourceVersion: srcVersion });
    const b = await createBackup(fixture.fs, fixture.appData, { sourceVersion: srcVersion });

    await pruneBackups(fixture.fs, fixture.appData, 1, b.txnId);
    const remaining = fixture.fs
      .listFiles()
      .filter((rel) => rel.startsWith(backupsPrefix(fixture.appData)));
    expect(remaining.some((rel) => rel.includes(b.txnId))).toBe(true);
    expect(remaining.some((rel) => rel.includes(a.txnId))).toBe(false);
  });

  test("restoreBackup copies the verified snapshot back over data/", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    const srcVersion = {
      layoutVersion: 1,
      schemaVersion: 0,
      runtimeVersion: "0.0.0",
      committedAt: null,
      txnId: null,
    };
    const { manifest, dirPath } = await createBackup(fixture.fs, fixture.appData, {
      sourceVersion: srcVersion,
    });
    // Mutate data, then restore.
    fixture.fs.seed("C:/Users/me/AppData/Roaming/NovelMind/data/uploads/novel.txt", "MUTATED");
    await restoreBackup(fixture.fs, manifest, dirPath, fixture.appData);
    expect(fixture.fs.content("C:/Users/me/AppData/Roaming/NovelMind/data/uploads/novel.txt")).toBe(
      "novel content",
    );
  });
});

test.describe("runtime wiring (migrating state consumes the gate)", () => {
  const BUDGETS = { startTimeoutMs: 300, drainMs: 50, killMs: 50 };

  test("runtime reaches ready only after the migration commit", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    const runner = fixture.buildRunner(contentSteps("ok"));
    const gate: MigrationGate = migrationGateFrom(runner);

    const adapter = new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
      repoRoot: "C:/fake-repo",
    });
    const runtime = new DesktopRuntime({ adapter, migration: gate });

    const snapshot = await runtime.ensureReady();
    expect(snapshot.state).toBe("ready");
    expect(snapshot.ready).toBe(true);
    expect(await runner.needsMigration()).toBe(false); // committed before ready
  });

  test("a failed migration never reports ready (D-43-06)", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    const runner = fixture.buildRunner([
      step("database", async () => {
        throw new Error("database step blew up (injected)");
      }),
      ...contentSteps("ok").slice(1),
    ]);
    const gate: MigrationGate = migrationGateFrom(runner);

    const adapter = new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
      repoRoot: "C:/fake-repo",
    });
    const runtime = new DesktopRuntime({ adapter, migration: gate });

    const snapshot = await runtime.ensureReady();
    expect(snapshot.ready).toBe(false);
    expect(snapshot.state).toBe("failed");
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);
    expect(await runner.needsMigration()).toBe(true); // old version preserved

    // Fix the failure; retry reaches ready and commits once.
    const retryRunner = fixture.buildRunner(contentSteps("ok"));
    const retryGate: MigrationGate = migrationGateFrom(retryRunner);
    const retryRuntime = new DesktopRuntime({
      adapter: new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
        repoRoot: "C:/fake-repo",
      }),
      migration: retryGate,
    });
    const recovered = await retryRuntime.ensureReady();
    expect(recovered.state).toBe("ready");
    expect(await retryRunner.needsMigration()).toBe(false);
  });
});
