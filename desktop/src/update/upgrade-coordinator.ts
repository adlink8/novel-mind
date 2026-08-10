/**
 * Versioned backup-first upgrade coordinator (Phase 45, plan 45-02, D-45-04,
 * T-45-02-01/T-45-02-03).
 *
 * A compatible desktop upgrade is a reversible, evidence-first transaction that
 * layers the upgrade decisions on top of the 43-03 `MigrationRunner`:
 *
 *   1. DETECT   — compare the committed version state (schema + runtime version)
 *                 with the running binary. Newer data than this binary supports
 *                 (schema or runtime version ahead) is a REGRESSION and is
 *                 refused BEFORE any write: an old version never opens new data.
 *                 Same-schema-but-older runtime is a metadata-only commit (the
 *                 running version is recorded without a data migration).
 *   2. PREPARE  — stop the owned runtime (injected), then verify the
 *                 checksum-pinned prior fixture against its declared SHA-256
 *                 inventory. A mismatched/tampered fixture rejects the upgrade
 *                 (T-45-02-01) and nothing is modified. Backup capacity is the
 *                 MigrationRunner's explicit INSUFFICIENT_SPACE gate.
 *   3. MIGRATE  — declared steps run in fixed order through the backup-first
 *                 runner; the atomic version commit advances the schema version
 *                 only after every step succeeded.
 *   4. VALIDATE — the injected post-upgrade domain probe re-checks the new data;
 *                 on probe failure the upgrade is rolled back so a failed
 *                 upgrade NEVER leaves a committed version (fail-closed).
 *   5. ROLLBACK — `rollback()` restores the verified pre-migration backup and
 *                 the source version state (user-selectable, D-45-04).
 *
 * The coordinator is Electron-free and unit-testable; the runtime/process
 * concerns (stopping the owned runtime, fixture roots) are injected. It must be
 * consumed by the main process during startup and by the update path — it is a
 * module-level capability, not a daemon.
 */
import {
  MIGRATION_JOURNAL_FILENAME,
  containPath,
  type AppDataPaths,
  type DataFs,
} from "../data/app-data-layout";
import {
  BACKUP_MANIFEST_FILENAME,
  hashFile,
  readBackupManifest,
  restoreBackup,
  verifyBackup,
  walkTree,
  type BackupManifest,
} from "../data/backup";
import {
  MigrationRunner,
  type MigrationStep,
  type MigrationStepId,
} from "../data/migration-runner";
import {
  defaultVersionState,
  readVersionState,
  writeVersionStateAtomic,
  type VersionState,
} from "../data/version-state";

export const UPGRADE_REFUSAL_CODES = {
  VERSION_REGRESSION: "VERSION_REGRESSION",
  FIXTURE_MISMATCH: "FIXTURE_MISMATCH",
  RUNTIME_STOP_FAILED: "RUNTIME_STOP_FAILED",
  POST_UPGRADE_PROBE_FAILED: "POST_UPGRADE_PROBE_FAILED",
} as const;
export type UpgradeRefusalCode = (typeof UPGRADE_REFUSAL_CODES)[keyof typeof UPGRADE_REFUSAL_CODES];

/**
 * Typed upgrade refusal. `oldDataPreserved` is always true — a refusal happens
 * before any migration writes, or (probe failure) restores the pre-upgrade state.
 */
export class UpgradeRefusedError extends Error {
  readonly code: UpgradeRefusalCode;
  readonly reason: string;
  readonly recoveryInstruction: string;
  readonly oldDataPreserved: true = true as const;

  constructor(args: {
    code: UpgradeRefusalCode;
    reason: string;
    recoveryInstruction: string;
    cause?: unknown;
  }) {
    const detail =
      args.cause instanceof Error ? args.cause.message : String(args.cause ?? "unknown");
    super(`upgrade refused [${args.code}]: ${args.reason} (${detail})`);
    this.name = "UpgradeRefusedError";
    this.code = args.code;
    this.reason = args.reason;
    this.recoveryInstruction = args.recoveryInstruction;
  }
}

/** Semantic version triple (major, minor, patch); unparseable → 0.0.0. */
export function parseVersion(value: string): readonly [number, number, number] {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(value.trim());
  if (match === null) return [0, 0, 0];
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

/** -1 when a < b, 0 equal, 1 when a > b (semver-aware, 0.0.0 fallback). */
export function compareAppVersions(a: string, b: string): number {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  for (let i = 0; i < 3; i += 1) {
    if (pa[i]! < pb[i]!) return -1;
    if (pa[i]! > pb[i]!) return 1;
  }
  return 0;
}

export type UpgradeDecision =
  | { kind: "current" }
  | {
      kind: "regression";
      reason: string;
      recoveryInstruction: string;
    }
  /** Same schema, running a newer app version → record the version only. */
  | { kind: "metadata-only" }
  /** Schema (or layout) below target → full backup-first migration. */
  | { kind: "upgrade-needed" };

/**
 * Decide what (if anything) an upgrade must do for the committed version state.
 * A null state is an uninitialized first run → upgrade-needed.
 */
export function evaluateUpgrade(
  state: VersionState | null,
  targetSchemaVersion: number,
  appVersion: string,
): UpgradeDecision {
  if (state === null) return { kind: "upgrade-needed" };
  if (state.schemaVersion > targetSchemaVersion) {
    return {
      kind: "regression",
      reason: `data schema version ${state.schemaVersion} is newer than this binary supports (${targetSchemaVersion})`,
      recoveryInstruction:
        "Install the matching or newer application version. Your data was NOT modified and will be " +
        "read again once the correct version runs.",
    };
  }
  if (state.schemaVersion < targetSchemaVersion) return { kind: "upgrade-needed" };
  // Same schema: only the runtime/app version can disagree.
  const cmp = compareAppVersions(state.runtimeVersion, appVersion);
  if (cmp > 0) {
    return {
      kind: "regression",
      reason: `data was last written by app version ${state.runtimeVersion}, which is newer than this binary (${appVersion})`,
      recoveryInstruction:
        "Install the application version that wrote this data (or newer). Your data was NOT " +
        "modified; it will be readable again once the correct version runs.",
    };
  }
  if (cmp < 0) return { kind: "metadata-only" };
  return { kind: "current" };
}

export interface UpgradeContext {
  fs: DataFs;
  appData: AppDataPaths;
  /** Committed state being upgraded FROM (pre-upgrade evidence). */
  sourceVersion: VersionState;
  targetSchemaVersion: number;
  appVersion: string;
  /**
   * True when an in-flight migration journal exists (a retry resuming from the
   * verified backup). Steps that already ran may have added data files, so a
   * checksum verifier must not reject undeclared files while resuming.
   */
  resumingFromJournal: boolean;
}

export interface UpgradeCoordinatorOptions {
  fs: DataFs;
  appData: AppDataPaths;
  targetSchemaVersion: number;
  /** Version string of the running binary (e.g. app.getVersion()). */
  appVersion: string;
  /** Declared migration steps in fixed order (43-03 MIGRATION_STEP_ORDER). */
  steps: readonly MigrationStep[];
  /** Backup retention; defaults to the runner's 5. */
  backupRetention?: number;
  /**
   * Verify the checksum-pinned prior fixture before migrating (T-45-02-01).
   * `createPinnedFixtureVerifier` provides the default implementation. Any throw
   * is converted into a typed FIXTURE_MISMATCH refusal with no data modified.
   */
  verifyFixture?: (ctx: UpgradeContext) => Promise<void>;
  /**
   * Post-upgrade domain probe. Runs after the version commit; a throw rolls the
   * upgrade back so a failed validation never leaves a committed version.
   */
  postUpgradeProbe?: (ctx: UpgradeContext) => Promise<void>;
  /**
   * Stop the owned runtime before migrating (D-45-04). Injected by the main
   * process; a throw refuses the upgrade with no data modified.
   */
  stopOwnedRuntime?: () => Promise<void>;
}

export type UpgradeOutcome =
  | { outcome: "current" }
  | { outcome: "metadata-only"; runtimeVersion: string }
  | {
      outcome: "upgraded";
      schemaVersion: number;
      runtimeVersion: string;
      txnId: string;
      backupDirPath: string;
      stepsDone: readonly MigrationStepId[];
    };

export type RollbackResult =
  | { ok: true; restoredTxnId: string; sourceSchemaVersion: number; restoredAt: string }
  | { ok: false; code: "NO_JOURNAL" | "NO_MANIFEST" | "BACKUP_CORRUPT"; recoveryInstruction: string };

export class UpgradeCoordinator {
  private readonly fs: DataFs;
  private readonly appData: AppDataPaths;
  private readonly targetSchemaVersion: number;
  private readonly appVersion: string;
  private readonly steps: readonly MigrationStep[];
  private readonly backupRetention: number | undefined;
  private readonly verifyFixture: ((ctx: UpgradeContext) => Promise<void>) | undefined;
  private readonly postUpgradeProbe: ((ctx: UpgradeContext) => Promise<void>) | undefined;
  private readonly stopOwnedRuntime: (() => Promise<void>) | undefined;

  constructor(options: UpgradeCoordinatorOptions) {
    this.fs = options.fs;
    this.appData = options.appData;
    this.targetSchemaVersion = options.targetSchemaVersion;
    this.appVersion = options.appVersion;
    this.steps = options.steps;
    this.backupRetention = options.backupRetention;
    this.verifyFixture = options.verifyFixture;
    this.postUpgradeProbe = options.postUpgradeProbe;
    this.stopOwnedRuntime = options.stopOwnedRuntime;
  }

  /**
   * Detect + run the upgrade. Idempotent: a committed state at/above the target
   * returns `current`; a failed attempt is resumed from the verified backup by
   * the runner. Throws `UpgradeRefusedError` for regressions and pre-migration
   * refusals, and propagates the typed `MigrationFailure` for in-flight failures.
   */
  async run(): Promise<UpgradeOutcome> {
    const state = await readVersionState(this.fs, this.appData.migrationMeta);
    const decision = evaluateUpgrade(state, this.targetSchemaVersion, this.appVersion);
    if (decision.kind === "regression") {
      throw new UpgradeRefusedError({
        code: UPGRADE_REFUSAL_CODES.VERSION_REGRESSION,
        reason: decision.reason,
        recoveryInstruction: decision.recoveryInstruction,
      });
    }
    if (decision.kind === "current") return { outcome: "current" };

    const sourceVersion = state ?? defaultVersionState();
    const runner = this.makeRunner();
    const journal = await runner.readJournal();
    const ctx: UpgradeContext = {
      fs: this.fs,
      appData: this.appData,
      sourceVersion,
      targetSchemaVersion: this.targetSchemaVersion,
      appVersion: this.appVersion,
      resumingFromJournal: journal !== null,
    };

    if (decision.kind === "metadata-only") {
      // No data migration: record the running version atomically.
      const next: VersionState = {
        ...sourceVersion,
        runtimeVersion: this.appVersion,
        committedAt: new Date().toISOString(),
        txnId: null,
      };
      await writeVersionStateAtomic(this.fs, this.appData.migrationMeta, next);
      return { outcome: "metadata-only", runtimeVersion: this.appVersion };
    }

    // upgrade-needed: stop the owned runtime, verify the fixture, then migrate.
    await this.stopRuntime();
    if (this.verifyFixture !== undefined) {
      try {
        await this.verifyFixture(ctx);
      } catch (cause) {
        throw new UpgradeRefusedError({
          code: UPGRADE_REFUSAL_CODES.FIXTURE_MISMATCH,
          reason: "checksum-pinned fixture verification failed; refusing to migrate possibly tampered data",
          recoveryInstruction:
            "Restore the original app data and retry the upgrade. Your data was NOT modified.",
          cause,
        });
      }
    }

    const result = await runner.run();
    if (!result.needsMigration) {
      // A concurrent commit landed between decision and run — idempotent no-op.
      return { outcome: "current" };
    }

    if (this.postUpgradeProbe !== undefined) {
      try {
        await this.postUpgradeProbe(ctx);
      } catch (cause) {
        const rollback = await this.rollback();
        const note = rollback.ok
          ? "The upgrade was rolled back to the pre-upgrade data and version."
          : `Rollback could not complete (${rollback.recoveryInstruction}). ` +
            "Restore from the backup named in the migration journal, then contact support.";
        throw new UpgradeRefusedError({
          code: UPGRADE_REFUSAL_CODES.POST_UPGRADE_PROBE_FAILED,
          reason: "post-upgrade domain validation failed",
          recoveryInstruction: `Fix the validation failure and retry the upgrade. ${note}`,
          cause,
        });
      }
    }

    return {
      outcome: "upgraded",
      schemaVersion: result.schemaVersion,
      runtimeVersion: this.appVersion,
      txnId: result.txnId,
      backupDirPath: result.backupDirPath,
      stepsDone: result.stepsDone,
    };
  }

  /**
   * User-selectable rollback (D-45-04): verify the pre-migration backup, restore
   * `data/` to EXACTLY the pre-migration state (including removing files the
   * migration added), restore the source version state and clear the journal so
   * a later upgrade starts from a clean, fresh backup.
   */
  async rollback(): Promise<RollbackResult> {
    const journal = await this.makeRunner().readJournal();
    if (journal === null || journal.backupDirPath === null || journal.backupDirPath === "") {
      return {
        ok: false,
        code: "NO_JOURNAL",
        recoveryInstruction:
          "No migration attempt is recorded; there is nothing to roll back.",
      };
    }
    const manifest = await readBackupManifest(
      this.fs,
      containPath(journal.backupDirPath, BACKUP_MANIFEST_FILENAME),
    );
    if (manifest === null) {
      return {
        ok: false,
        code: "NO_MANIFEST",
        recoveryInstruction:
          `The pre-migration backup manifest is unreadable at '${journal.backupDirPath}'. ` +
          "Stop and contact support before touching this data.",
      };
    }
    try {
      await verifyBackup(this.fs, manifest, journal.backupDirPath);
    } catch (cause) {
      return {
        ok: false,
        code: "BACKUP_CORRUPT",
        recoveryInstruction:
          "The pre-migration backup failed its hash verification. Your current data was NOT " +
          "modified. Restore from an older intact backup or contact support.",
      };
    }

    await restoreExactData(this.fs, manifest, journal.backupDirPath, this.appData);
    await writeVersionStateAtomic(this.fs, this.appData.migrationMeta, manifest.sourceVersion);
    // Clear the journal: the attempt is fully undone, so a retry backs up fresh.
    await this.fs
      .rm(containPath(this.appData.runtime, MIGRATION_JOURNAL_FILENAME), { force: true })
      .catch(() => undefined);

    return {
      ok: true,
      restoredTxnId: journal.txnId,
      sourceSchemaVersion: manifest.sourceVersion.schemaVersion,
      restoredAt: new Date().toISOString(),
    };
  }

  private makeRunner(): MigrationRunner {
    return new MigrationRunner({
      fs: this.fs,
      appData: this.appData,
      targetSchemaVersion: this.targetSchemaVersion,
      steps: this.steps,
      backupRetention: this.backupRetention,
      appVersion: this.appVersion,
    });
  }

  private async stopRuntime(): Promise<void> {
    if (this.stopOwnedRuntime === undefined) return;
    try {
      await this.stopOwnedRuntime();
    } catch (cause) {
      throw new UpgradeRefusedError({
        code: UPGRADE_REFUSAL_CODES.RUNTIME_STOP_FAILED,
        reason: "the owned runtime could not be stopped before migrating",
        recoveryInstruction:
          "Close all NovelMind windows and stop other NovelMind instances, then retry the " +
          "upgrade. Your data was NOT modified.",
        cause,
      });
    }
  }
}

/** Restore `data/` to exactly the backup snapshot (backup files + removal of extras). */
async function restoreExactData(
  fs: DataFs,
  manifest: BackupManifest,
  backupDirPath: string,
  appData: AppDataPaths,
): Promise<void> {
  await restoreBackup(fs, manifest, backupDirPath, appData);
  const declared = new Set(manifest.entries.map((entry) => entry.relPath));
  // Remove files a failed migration added that are not in the backup snapshot.
  const current = await walkTree(fs, appData.data, "data");
  for (const rel of current) {
    if (declared.has(rel)) continue;
    await fs.rm(containPath(appData.data, rel.slice("data/".length)), { force: true });
  }
  // Prune empty directories (bottom-up) so the restore is EXACT.
    await pruneEmptyDirectories(fs, appData.data);
}

/** Recursively remove empty directories under `root` (bottom-up, best-effort). */
async function pruneEmptyDirectories(fs: DataFs, root: string): Promise<void> {
  let names: string[];
  try {
    names = await fs.readdir(root);
  } catch {
    return; // root itself is gone or unreadable — nothing to prune here
  }
  for (const name of names) {
    const abs = containPath(root, name);
    const stat = await fs.stat(abs).catch(() => null);
    if (stat === null || !stat.isDirectory()) continue;
    await pruneEmptyDirectories(fs, abs);
    const remaining = await fs.readdir(abs).catch(() => []);
    if (remaining.length === 0) {
      // recursive: true is required to remove a directory (even an empty one)
      // with node:fs rm (ERR_FS_EISDIR otherwise).
      await fs.rm(abs, { recursive: true, force: true }).catch(() => undefined);
    }
  }
}

/**
 * Checksum-pinned fixture manifest produced by
 * `desktop/scripts/create-upgrade-fixture.ps1`. Every fixture file (data,
 * resources and the version metadata) is listed with its sha256 + size.
 */
export interface PinnedFixtureManifest {
  version: 1;
  schemaVersion: number;
  runtimeVersion: string;
  createdAt: string;
  fileCount: number;
  totalBytes: number;
  files: readonly { path: string; sha256: string; size: number }[];
}

export function readPinnedFixtureManifest(
  fs: DataFs,
  manifestPath: string,
): Promise<PinnedFixtureManifest | null> {
  return fs
    .readFile(manifestPath)
    .then((raw) => {
      try {
        const parsed: unknown = JSON.parse(raw);
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          (parsed as { version?: unknown }).version !== 1 ||
          !Array.isArray((parsed as { files?: unknown }).files) ||
          (parsed as { files?: unknown[] }).files!.some(
            (entry) =>
              typeof entry !== "object" ||
              entry === null ||
              typeof (entry as { path?: unknown }).path !== "string" ||
              typeof (entry as { sha256?: unknown }).sha256 !== "string" ||
              typeof (entry as { size?: unknown }).size !== "number",
          )
        ) {
          return null;
        }
        return parsed as PinnedFixtureManifest;
      } catch {
        return null;
      }
    })
    .catch(() => null);
}

/**
 * Default fixture verifier (T-45-02-01): every declared entry must reproduce its
 * sha256/size at the resolved location (`data/...` under the app-data data root,
 * everything else under the fixture root). For a FRESH upgrade (no in-flight
 * journal) `data/` must also contain no undeclared file — a strong tamper gate.
 * While RESUMING from a verified backup, undeclared files are tolerated because
 * an earlier partial attempt's steps may already have added them; the declared
 * entries are still hash-checked (T-45-02-03).
 */
export function createPinnedFixtureVerifier(options: {
  fs: DataFs;
  /** Absolute path of fixture-manifest.json. */
  manifestPath: string;
  /** Absolute root of the fixture tree (base for resources/… and migration.json). */
  fixtureRoot: string;
  /** Absolute path of the mutable data root being upgraded (appData.data). */
  dataRoot: string;
}): (ctx: UpgradeContext) => Promise<void> {
  return async (ctx) => {
    const manifest = await readPinnedFixtureManifest(options.fs, options.manifestPath);
    if (manifest === null) {
      throw new Error("checksum-pinned fixture manifest is missing or invalid");
    }
    const declared = new Map(manifest.files.map((entry) => [entry.path, entry]));
    for (const entry of manifest.files) {
      const abs = entry.path.startsWith("data/")
        ? containPath(options.dataRoot, entry.path.slice("data/".length))
        : containPath(options.fixtureRoot, entry.path);
      let actual: { hash: string; size: number };
      try {
        actual = await hashFile(options.fs, abs);
      } catch (cause) {
        throw new Error(`fixture file missing: ${entry.path}`, { cause });
      }
      if (actual.hash !== entry.sha256 || actual.size !== entry.size) {
        throw new Error(`fixture checksum mismatch: ${entry.path}`);
      }
    }
    if (ctx.resumingFromJournal) return;
    const dataRels = await walkTree(options.fs, options.dataRoot, "data");
    for (const rel of dataRels) {
      if (!declared.has(rel)) {
        throw new Error(`fixture data contains undeclared file: ${rel}`);
      }
    }
  };
}
