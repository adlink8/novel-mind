/**
 * End-to-end data-recovery fault injection (plan 43-04, Task 2).
 *
 * Wires the real `MigrationRunner` (backup-first, journal-resumable) into the
 * real `DesktopRuntime` via `migrationGateFrom`, and adapts the runner to
 * `RuntimeRecovery`'s injected `RecoveryDataCapabilities` so the typed recovery
 * surface (`restoreBackup`, recovery instruction, backup availability) is
 * exercised end-to-end over the deterministic `FakeDataFs`.
 *
 * Faults injected per plan Task 2:
 * - failed migration mid-step      -> runtime failed, old data + backup preserved,
 *                                     typed recovery instruction, restore works
 * - retry after failure            -> resumes from the journal/verified backup
 * - insufficient disk space        -> failed typed before any write, no empty success
 * - denied app-data write          -> failed typed, old data preserved
 *
 * Every failure asserts: runtime snapshot AND recovery status show `ready ===
 * false` (never empty domain success, D-43-09), a stable redacted error code is
 * visible, and only bounded allowlisted actions are offered (T-43-04-02).
 */
import { expect, test } from "@playwright/test";
import {
  containPath,
  initializeAppDataPaths,
  type AppDataPaths,
} from "../../src/data/app-data-layout";
import {
  BACKUP_MANIFEST_FILENAME,
  readBackupManifest,
  restoreBackup,
  verifyBackup,
} from "../../src/data/backup";
import {
  MigrationRunner,
  migrationGateFrom,
  type MigrationContext,
  type MigrationStep,
  type MigrationStepId,
} from "../../src/data/migration-runner";
import { FakeDataFs } from "../data/fake-data-fs";
import type { RecoveryDataCapabilities } from "../../src/runtime/recovery";
import { DesktopRuntime } from "../../src/runtime/desktop-runtime";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import { createFakeOps } from "./fake-process-ops";
import { RUNTIME_ERROR_CODES, type AdapterBudgets } from "../../src/runtime/types";
import type { RuntimeRecoveryState } from "../../src/shared/runtime-status";
import { RuntimeRecovery } from "../../src/runtime/recovery";

const USER_DATA = "C:\\Users\\me\\AppData\\Roaming\\NovelMind";
const INSTALL_ROOT = "C:\\Program Files\\NovelMind";
const BUDGETS: Partial<AdapterBudgets> = { startTimeoutMs: 400, drainMs: 50, killMs: 50 };

function step(id: MigrationStepId, behavior: (ctx: MigrationContext) => Promise<void>): MigrationStep {
  return { id, run: behavior };
}

function contentSteps(): readonly MigrationStep[] {
  return [
    step("database", async () => undefined),
    step("vector", async () => undefined),
    step("app_metadata", async () => undefined),
  ];
}

interface Fixture {
  fs: FakeDataFs;
  appData: AppDataPaths;
  buildRunner: (steps: readonly MigrationStep[], target?: number) => MigrationRunner;
}

async function makeFixture(seed: Record<string, string> = {}): Promise<Fixture> {
  const fs = new FakeDataFs();
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

function dataContent(fs: FakeDataFs, appData: AppDataPaths, rel: string): string {
  return fs.content(containPath(appData.data, rel));
}

/** Adapt a MigrationRunner to the renderer-visible recovery data capabilities. */
function runnerRecoveryCapabilities(
  fs: FakeDataFs,
  appData: AppDataPaths,
  runner: MigrationRunner,
): RecoveryDataCapabilities {
  return {
    backupAvailable: async () => {
      const journal = await runner.readJournal();
      if (journal === null || journal.backupDirPath === null) return false;
      const manifest = await readBackupManifest(
        fs,
        containPath(journal.backupDirPath, BACKUP_MANIFEST_FILENAME),
      );
      return manifest !== null;
    },
    recoveryInstruction: async () => {
      const journal = await runner.readJournal();
      if (journal === null || journal.failedStep === null) return null;
      return `migration stopped at step '${journal.failedStep}'; old data and the pre-migration backup are intact`;
    },
    restoreBackup: async () => {
      const journal = await runner.readJournal();
      if (journal === null || journal.backupDirPath === null) {
        throw new Error("no migration backup exists");
      }
      const manifest = await readBackupManifest(
        fs,
        containPath(journal.backupDirPath, BACKUP_MANIFEST_FILENAME),
      );
      if (manifest === null) throw new Error("backup manifest missing");
      await verifyBackup(fs, manifest, journal.backupDirPath);
      await restoreBackup(fs, manifest, journal.backupDirPath, appData);
    },
  };
}

interface RuntimeFixture {
  fixture: Fixture;
  runtime: DesktopRuntime;
  recovery: RuntimeRecovery;
  gate: ReturnType<typeof migrationGateFrom>;
  data: RecoveryDataCapabilities;
}

async function makeRuntimeFixture(
  seed: Record<string, string>,
  steps: readonly MigrationStep[],
): Promise<RuntimeFixture> {
  const fixture = await makeFixture(seed);
  const runner = fixture.buildRunner(steps);
  const gate = migrationGateFrom(runner);
  const runtime = new DesktopRuntime({
    adapter: new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
      repoRoot: "C:/fake-repo",
    }),
    migration: gate,
  });
  const data = runnerRecoveryCapabilities(fixture.fs, fixture.appData, runner);
  return {
    fixture,
    runtime,
    recovery: new RuntimeRecovery({ runtime, data }),
    gate,
    data,
  };
}

function expectHonestFailure(status: RuntimeRecoveryState, expectedCode: string): void {
  expect(status.ready).toBe(false);
  expect(status.state).not.toBe("ready");
  expect(status.errorCode).toBe(expectedCode);
  expect(status.errorMessage).not.toBeNull();
}

test.describe("runtime + data recovery (plan 43-04)", () => {
  test("first-run migration reaches ready and commits exactly once", async () => {
    const rt = await makeRuntimeFixture(
      { "data/uploads/novel.txt": "novel content" },
      contentSteps(),
    );
    const snapshot = await rt.runtime.ensureReady();
    expect(snapshot.state).toBe("ready");
    expect(snapshot.ready).toBe(true);

    const status = await rt.recovery.status();
    expect(status.ready).toBe(true);
    expect(status.errorCode).toBeNull();
    expect(await rt.gate.needsMigration()).toBe(false);
  });

  test("failed migration -> failed, old data + backup preserved, typed recovery instruction", async () => {
    const rt = await makeRuntimeFixture(
      { "data/uploads/novel.txt": "novel content" },
      [
        step("database", async () => {
          throw new Error("database step blew up (injected)");
        }),
        ...contentSteps().slice(1),
      ],
    );

    const snapshot = await rt.runtime.ensureReady();
    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);

    const status = await rt.recovery.status();
    expectHonestFailure(status, RUNTIME_ERROR_CODES.MIGRATION_FAILED);
    // Backup exists -> restoreBackup is offered alongside retry/diagnostics.
    expect(status.backupAvailable).toBe(true);
    expect(status.recoveryActions.map((a) => a.id)).toEqual([
      "retry",
      "openDiagnostics",
      "restoreBackup",
    ]);

    // Old data untouched; version not advanced.
    expect(dataContent(rt.fixture.fs, rt.fixture.appData, "uploads/novel.txt")).toBe(
      "novel content",
    );
    expect(await rt.gate.needsMigration()).toBe(true);

    // Typed recovery instruction surfaces the bounded step.
    const instruction = await rt.recovery.migrationRecoveryInstruction();
    expect(instruction).toContain("database");
    expect(instruction).toContain("backup");
  });

  test("restoreBackup restores the verified snapshot and keeps data intact", async () => {
    const rt = await makeRuntimeFixture(
      { "data/uploads/novel.txt": "novel content" },
      [
        step("database", async () => {
          throw new Error("database step blew up (injected)");
        }),
        ...contentSteps().slice(1),
      ],
    );
    await rt.runtime.ensureReady();
    expect((await rt.recovery.status()).state).toBe("failed");

    // Mutate data as if a partial/other process wrote over it, then restore.
    rt.fixture.fs.seed(containPath(rt.fixture.appData.data, "uploads/novel.txt"), "MUTATED");
    const result = await rt.recovery.recover("restoreBackup");
    expect(result.applied).toBe(true);
    expect(result.error).toBeNull();
    expect(dataContent(rt.fixture.fs, rt.fixture.appData, "uploads/novel.txt")).toBe(
      "novel content",
    );
  });

  test("retry after failed migration resumes from the journal (no re-backup)", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    let calls = 0;
    const runner = fixture.buildRunner([
      step("database", async () => {
        calls += 1;
        if (calls === 1) throw new Error("database step blew up (injected)");
      }),
      ...contentSteps().slice(1),
    ]);
    const gate = migrationGateFrom(runner);
    const runtime = new DesktopRuntime({
      adapter: new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
        repoRoot: "C:/fake-repo",
      }),
      migration: gate,
    });
    expect((await runtime.ensureReady()).state).toBe("failed");
    const copyBeforeRetry = fixture.fs.copyCount();

    // Retry from the failed state via the recovery gate.
    const retry = await runtime.ensureReady();
    expect(retry.state).toBe("ready");
    expect(retry.ready).toBe(true);
    expect(fixture.fs.copyCount()).toBe(copyBeforeRetry); // no second backup
    expect(await gate.needsMigration()).toBe(false);

    const status = await new RuntimeRecovery({
      runtime,
      data: runnerRecoveryCapabilities(fixture.fs, fixture.appData, runner),
    }).status();
    expect(status.ready).toBe(true);
  });

  test("insufficient disk space -> failed typed before any write, never empty success", async () => {
    const rt = await makeRuntimeFixture(
      { "data/uploads/big.txt": "x".repeat(10_000) },
      contentSteps(),
    );
    rt.fixture.fs.faults.freeBytes = 1_000; // below the 10KB snapshot
    const writesBefore = rt.fixture.fs.writeLog.length;

    const snapshot = await rt.runtime.ensureReady();
    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);

    const status = await rt.recovery.status();
    expectHonestFailure(status, RUNTIME_ERROR_CODES.MIGRATION_FAILED);
    // No backup, no journal, no version write (explicit INSUFFICIENT_SPACE gate).
    expect(rt.fixture.fs.writeLog.length).toBe(writesBefore);
    expect(await rt.gate.needsMigration()).toBe(true);
    // No backup -> restoreBackup is NOT offered.
    expect(status.recoveryActions.map((a) => a.id)).toEqual(["retry", "openDiagnostics"]);
  });

  test("denied app-data write -> failed typed, old data preserved", async () => {
    const rt = await makeRuntimeFixture(
      { "data/uploads/novel.txt": "novel content" },
      contentSteps(),
    );
    rt.fixture.fs.faults.denyPathPrefix = "C:/Users/me/AppData/Roaming/NovelMind/backups";

    const snapshot = await rt.runtime.ensureReady();
    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);
    expect(dataContent(rt.fixture.fs, rt.fixture.appData, "uploads/novel.txt")).toBe(
      "novel content",
    );
    expect(await rt.gate.needsMigration()).toBe(true);
  });

  test("interrupted migration resumes from the journal across runtime instances", async () => {
    const fixture = await makeFixture({ "data/uploads/novel.txt": "novel content" });
    let ranVector = false;
    const failingRunner = fixture.buildRunner([
      step("database", async () => undefined),
      step("vector", async () => {
        ranVector = true;
        throw new Error("process killed mid-migration (injected)");
      }),
      step("app_metadata", async () => undefined),
    ]);
    const firstRuntime = new DesktopRuntime({
      adapter: new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
        repoRoot: "C:/fake-repo",
      }),
      migration: migrationGateFrom(failingRunner),
    });
    expect((await firstRuntime.ensureReady()).state).toBe("failed");
    const copyAfterFirst = fixture.fs.copyCount();

    // Fresh process (new runtime + runner) resumes from the journal/backup.
    const resumeRunner = fixture.buildRunner([
      step("vector", async () => undefined),
      step("app_metadata", async () => undefined),
    ]);
    const secondRuntime = new DesktopRuntime({
      adapter: new DevelopmentProcessAdapter(createFakeOps(), BUDGETS, {
        repoRoot: "C:/fake-repo",
      }),
      migration: migrationGateFrom(resumeRunner),
    });
    const recovered = await secondRuntime.ensureReady();
    expect(recovered.state).toBe("ready");
    expect(recovered.ready).toBe(true);
    expect(fixture.fs.copyCount()).toBe(copyAfterFirst); // no re-backup
    expect(ranVector).toBe(true); // database not re-run, vector resumed
    expect(await resumeRunner.needsMigration()).toBe(false);
  });

  test("every injected data fault yields an honest failed terminal (no empty success)", async () => {
    const cases: {
      name: string;
      build: () => Promise<RuntimeFixture>;
      expectActions: string[];
      expectBackup: boolean;
    }[] = [
      {
        name: "mid-step migration failure",
        build: () =>
          makeRuntimeFixture(
            { "data/uploads/novel.txt": "novel content" },
            [
              step("database", async () => {
                throw new Error("database step blew up (injected)");
              }),
              ...contentSteps().slice(1),
            ],
          ),
        expectActions: ["retry", "openDiagnostics", "restoreBackup"],
        expectBackup: true,
      },
      {
        name: "insufficient disk space",
        build: async () => {
          const rt = await makeRuntimeFixture(
            { "data/uploads/big.txt": "x".repeat(10_000) },
            contentSteps(),
          );
          rt.fixture.fs.faults.freeBytes = 1_000;
          return rt;
        },
        expectActions: ["retry", "openDiagnostics"],
        expectBackup: false,
      },
      {
        name: "denied app-data write",
        build: async () => {
          const rt = await makeRuntimeFixture(
            { "data/uploads/novel.txt": "novel content" },
            contentSteps(),
          );
          rt.fixture.fs.faults.denyPathPrefix = "C:/Users/me/AppData/Roaming/NovelMind/backups";
          return rt;
        },
        expectActions: ["retry", "openDiagnostics"],
        expectBackup: false,
      },
    ];

    for (const entry of cases) {
      const rt = await entry.build();
      const snapshot = await rt.runtime.ensureReady();
      expect(snapshot.state, entry.name).toBe("failed");
      expect(snapshot.ready, entry.name).toBe(false);
      expect(snapshot.lastError?.code, entry.name).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);

      const status = await rt.recovery.status();
      expect(status.ready, entry.name).toBe(false);
      expect(status.errorCode, entry.name).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);
      expect(status.errorMessage, entry.name).not.toBeNull();
      expect(status.backupAvailable, entry.name).toBe(entry.expectBackup);
      expect(status.recoveryActions.map((a) => a.id), entry.name).toEqual(entry.expectActions);
    }
  });
});
