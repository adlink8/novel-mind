/**
 * Backup-first migration transaction (Phase 43, plan 43-03, D-43-06,
 * T-43-03-01).
 *
 * A migration is a recoverable transaction:
 *
 *   1. BACKUP  — hash-backed snapshot of `data/` is created BEFORE anything is
 *                migrated (evidence precedes version advancement).
 *   2. MIGRATE — declared steps run in fixed order (files → database → vector →
 *                app_metadata). Steps are injected, never owned: the desktop
 *                runtime is not a database authority (D-43-04).
 *   3. COMMIT  — the new layout/schema/runtime version is written atomically
 *                (tmp + rename). Only after this does `needsMigration()` return
 *                false, so the runtime can report ready.
 *   4. FAILURE — old data is never deleted or overwritten in place; the backup
 *                stays and a typed `MigrationFailure` carries a bounded
 *                recovery instruction. The journal lets a retry resume from
 *                the already-verified backup (idempotent).
 *
 * The journal (`<runtime>/migration-journal.json`) is the retry cursor: an
 * interrupted or failed attempt records its txn, the step it stopped at and the
 * backup directory, so a subsequent `run()` reuses the verified evidence and
 * continues from the first un-done step instead of re-migrating or re-backing up.
 */
import {
  APP_DATA_LAYOUT_VERSION,
  MIGRATION_JOURNAL_FILENAME,
  containPath,
  type AppDataPaths,
  type DataFs,
} from "./app-data-layout";
import path from "node:path";
import type { VersionState } from "./version-state";
import { readVersionState, writeVersionStateAtomic, defaultVersionState } from "./version-state";
import {
  BackupError,
  BACKUP_ERROR_CODES,
  BACKUP_MANIFEST_FILENAME,
  createBackup,
  hashFile,
  readBackupManifest,
  verifyBackup,
} from "./backup";
import type { BackupManifest } from "./backup";
import type { MigrationGate } from "../runtime/types";

export const MIGRATION_STEP_IDS = ["files", "database", "vector", "app_metadata"] as const;
export type MigrationStepId = (typeof MIGRATION_STEP_IDS)[number];

/** Fixed step order. Steps may be skipped/injected per deployment, but never reordered. */
export const MIGRATION_STEP_ORDER: readonly MigrationStepId[] = [...MIGRATION_STEP_IDS];

export interface MigrationContext {
  appData: AppDataPaths;
  fs: DataFs;
  /** Committed state being migrated FROM (pre-migration evidence). */
  sourceVersion: VersionState;
  targetSchemaVersion: number;
  txnId: string;
  backupDirPath: string;
  backupManifest: BackupManifest;
}

export interface MigrationStep {
  id: MigrationStepId;
  run(ctx: MigrationContext): Promise<void>;
}

export const MIGRATION_FAILURE_CODES = {
  BACKUP_FAILED: "BACKUP_FAILED",
  INSUFFICIENT_SPACE: "INSUFFICIENT_SPACE",
  STEP_FAILED: "STEP_FAILED",
  COMMIT_FAILED: "COMMIT_FAILED",
  JOURNAL_FAILED: "JOURNAL_FAILED",
} as const;
export type MigrationFailureCode =
  (typeof MIGRATION_FAILURE_CODES)[keyof typeof MIGRATION_FAILURE_CODES];

export type MigrationPhase = "backing-up" | "migrating" | "committing" | "done" | "failed";

export interface JournalRecord {
  version: 1;
  txnId: string;
  phase: MigrationPhase;
  sourceSchemaVersion: number;
  targetSchemaVersion: number;
  backupDirPath: string;
  stepsDone: MigrationStepId[];
  failedStep: MigrationStepId | null;
  failureCode: MigrationFailureCode | null;
  updatedAt: string;
}

/** Typed migration failure. `oldDataPreserved` is always true — never a lie. */
export class MigrationFailure extends Error {
  readonly code: MigrationFailureCode;
  readonly step: MigrationStepId | null;
  readonly txnId: string | null;
  readonly backupDirPath: string | null;
  readonly recoveryInstruction: string;
  readonly oldDataPreserved: true = true as const;

  constructor(args: {
    code: MigrationFailureCode;
    step: MigrationStepId | null;
    txnId: string | null;
    backupDirPath: string | null;
    recoveryInstruction: string;
    cause?: unknown;
  }) {
    const detail =
      args.cause instanceof Error ? args.cause.message : String(args.cause ?? "unknown");
    super(
      `migration failed [${args.code}]${args.step !== null ? ` at step ${args.step}` : ""}: ${detail}`,
    );
    this.name = "MigrationFailure";
    this.code = args.code;
    this.step = args.step;
    this.txnId = args.txnId;
    this.backupDirPath = args.backupDirPath;
    this.recoveryInstruction = args.recoveryInstruction;
  }
}

export type MigrationResult =
  | { needsMigration: false }
  | {
      needsMigration: true;
      schemaVersion: number;
      txnId: string;
      backupDirPath: string;
      stepsDone: readonly MigrationStepId[];
    };

export interface MigrationRunnerOptions {
  fs: DataFs;
  appData: AppDataPaths;
  /** Schema version this run migrates TO. */
  targetSchemaVersion: number;
  /** Steps in declared order. The `files` step may use `createFilesCopyStep`. */
  steps: readonly MigrationStep[];
  /** Backup retention; defaults to 5. */
  backupRetention?: number;
  /** App version string recorded on commit (falls back to the source value). */
  appVersion?: string;
}

export class MigrationRunner {
  private readonly fs: DataFs;
  private readonly appData: AppDataPaths;
  private readonly targetSchemaVersion: number;
  private readonly steps: readonly MigrationStep[];
  private readonly backupRetention: number;
  private readonly appVersion: string;

  constructor(options: MigrationRunnerOptions) {
    this.fs = options.fs;
    this.appData = options.appData;
    this.targetSchemaVersion = options.targetSchemaVersion;
    this.steps = [...options.steps].sort(
      (a, b) => MIGRATION_STEP_ORDER.indexOf(a.id) - MIGRATION_STEP_ORDER.indexOf(b.id),
    );
    this.backupRetention = options.backupRetention ?? 5;
    this.appVersion = options.appVersion ?? "";
  }

  /** True when the committed schema version is below the target (D-43-06). */
  async needsMigration(): Promise<boolean> {
    const state = await readVersionState(this.fs, this.appData.migrationMeta);
    return (state?.schemaVersion ?? 0) < this.targetSchemaVersion;
  }

  /**
   * Run the backup-first migration transaction. Idempotent: a committed state
   * at/above the target returns immediately, and a failed/interrupted attempt
   * resumes from its verified backup.
   */
  async run(): Promise<MigrationResult> {
    const state = await readVersionState(this.fs, this.appData.migrationMeta);
    const sourceSchema = state?.schemaVersion ?? 0;
    if (sourceSchema >= this.targetSchemaVersion) {
      return { needsMigration: false };
    }
    const sourceVersion = state ?? defaultVersionState();

    // Resume an in-flight attempt when its backup is intact.
    const journal = await this.readJournal();
    let resume: { journal: JournalRecord; manifest: BackupManifest } | null = null;
    if (
      journal !== null &&
      journal.targetSchemaVersion === this.targetSchemaVersion &&
      journal.sourceSchemaVersion === sourceSchema
    ) {
      const manifest = await readBackupManifest(
        this.fs,
        containPath(journal.backupDirPath, BACKUP_MANIFEST_FILENAME),
      );
      if (manifest === null) {
        // The journal claims a backup but its manifest cannot be read. Refuse
        // to proceed silently: the evidence is corrupt (T-43-03-01).
        throw new MigrationFailure({
          code: MIGRATION_FAILURE_CODES.BACKUP_FAILED,
          step: journal.failedStep,
          txnId: journal.txnId,
          backupDirPath: journal.backupDirPath,
          recoveryInstruction:
            "The pre-migration backup manifest is unreadable or corrupt. Your old data was NOT " +
            "modified. Stop, inspect the backup directory, and contact support before retrying.",
        });
      }
      try {
        await verifyBackup(this.fs, manifest, journal.backupDirPath);
        resume = { journal, manifest };
      } catch (cause) {
        if (cause instanceof BackupError) {
          throw new MigrationFailure({
            code: MIGRATION_FAILURE_CODES.BACKUP_FAILED,
            step: journal.failedStep,
            txnId: journal.txnId,
            backupDirPath: journal.backupDirPath,
            recoveryInstruction:
              "Existing migration backup is corrupted (hash mismatch). Your old data was NOT modified. " +
              "Stop, restore from an older intact backup or contact support before retrying.",
            cause,
          });
        }
        throw cause;
      }
    }

    // Fresh attempt: backup first, then journal.
    let activeJournal: JournalRecord;
    let activeManifest: BackupManifest;
    if (resume === null) {
      let backup;
      try {
        backup = await createBackup(this.fs, this.appData, {
          sourceVersion,
          retention: this.backupRetention,
        });
      } catch (cause) {
        if (cause instanceof BackupError && cause.code === BACKUP_ERROR_CODES.INSUFFICIENT_SPACE) {
          throw new MigrationFailure({
            code: MIGRATION_FAILURE_CODES.INSUFFICIENT_SPACE,
            step: null,
            txnId: null,
            backupDirPath: null,
            recoveryInstruction:
              "Not enough free disk space for the pre-migration backup. Free space on the " +
              "drive holding the app-data directory, then retry. Your data was NOT modified.",
            cause,
          });
        }
        throw new MigrationFailure({
          code: MIGRATION_FAILURE_CODES.BACKUP_FAILED,
          step: null,
          txnId: null,
          backupDirPath: null,
          recoveryInstruction:
            "The pre-migration backup could not be created. Your data was NOT modified. " +
            "Retry the upgrade; if it persists, contact support with the diagnostic logs.",
          cause,
        });
      }
      activeJournal = {
        version: 1,
        txnId: backup.txnId,
        phase: "migrating",
        sourceSchemaVersion: sourceSchema,
        targetSchemaVersion: this.targetSchemaVersion,
        backupDirPath: backup.dirPath,
        stepsDone: [],
        failedStep: null,
        failureCode: null,
        updatedAt: new Date().toISOString(),
      };
      activeManifest = backup.manifest;
    } else {
      activeJournal = { ...resume.journal, phase: "migrating", updatedAt: new Date().toISOString() };
      activeManifest = resume.manifest;
    }

    await this.writeJournal(activeJournal);

    const ctx: MigrationContext = {
      appData: this.appData,
      fs: this.fs,
      sourceVersion,
      targetSchemaVersion: this.targetSchemaVersion,
      txnId: activeJournal.txnId,
      backupDirPath: activeJournal.backupDirPath,
      backupManifest: activeManifest,
    };

    // Migrate declared steps in order.
    const done = new Set<MigrationStepId>(activeJournal.stepsDone);
    for (const step of this.steps) {
      if (done.has(step.id)) continue;
      try {
        await step.run(ctx);
      } catch (cause) {
        activeJournal.phase = "failed";
        activeJournal.failedStep = step.id;
        activeJournal.failureCode = MIGRATION_FAILURE_CODES.STEP_FAILED;
        activeJournal.updatedAt = new Date().toISOString();
        await this.writeJournal(activeJournal);
        throw new MigrationFailure({
          code: MIGRATION_FAILURE_CODES.STEP_FAILED,
          step: step.id,
          txnId: activeJournal.txnId,
          backupDirPath: activeJournal.backupDirPath,
          recoveryInstruction:
            `Migration stopped during step '${step.id}'. Your old data and the pre-migration ` +
            `backup at '${activeJournal.backupDirPath}' are intact. Fix the cause, then retry — ` +
            "the retry resumes from the verified backup.",
          cause,
        });
      }
      done.add(step.id);
      activeJournal.stepsDone = [...done];
      activeJournal.updatedAt = new Date().toISOString();
      await this.writeJournal(activeJournal);
    }

    // Atomic version commit — the only step that advances committed state.
    activeJournal.phase = "committing";
    await this.writeJournal(activeJournal);
    try {
      const nextState: VersionState = {
        layoutVersion: APP_DATA_LAYOUT_VERSION,
        schemaVersion: this.targetSchemaVersion,
        runtimeVersion:
          this.appVersion !== "" ? this.appVersion : sourceVersion.runtimeVersion,
        committedAt: new Date().toISOString(),
        txnId: activeJournal.txnId,
      };
      await writeVersionStateAtomic(this.fs, this.appData.migrationMeta, nextState);
    } catch (cause) {
      activeJournal.phase = "failed";
      activeJournal.failedStep = null;
      activeJournal.failureCode = MIGRATION_FAILURE_CODES.COMMIT_FAILED;
      activeJournal.updatedAt = new Date().toISOString();
      await this.writeJournal(activeJournal);
      throw new MigrationFailure({
        code: MIGRATION_FAILURE_CODES.COMMIT_FAILED,
        step: null,
        txnId: activeJournal.txnId,
        backupDirPath: activeJournal.backupDirPath,
        recoveryInstruction:
          "The new version could not be committed. Your old data and the pre-migration backup " +
          "are intact. Retry, or restore from the backup at " +
          `'${activeJournal.backupDirPath}' and contact support.`,
        cause,
      });
    }

    activeJournal.phase = "done";
    activeJournal.updatedAt = new Date().toISOString();
    await this.writeJournal(activeJournal);

    return {
      needsMigration: true,
      schemaVersion: this.targetSchemaVersion,
      txnId: activeJournal.txnId,
      backupDirPath: activeJournal.backupDirPath,
      stepsDone: activeJournal.stepsDone,
    };
  }

  /** Current journal record, or null when no attempt has started. */
  async readJournal(): Promise<JournalRecord | null> {
    const filePath = containPath(this.appData.runtime, MIGRATION_JOURNAL_FILENAME);
    let raw: string;
    try {
      raw = await this.fs.readFile(filePath);
    } catch {
      return null;
    }
    try {
      const parsed = JSON.parse(raw) as JournalRecord;
      if (
        typeof parsed !== "object" ||
        parsed === null ||
        parsed.version !== 1 ||
        typeof parsed.txnId !== "string"
      ) {
        return null;
      }
      return parsed;
    } catch {
      return null;
    }
  }

  private async writeJournal(journal: JournalRecord): Promise<void> {
    const filePath = containPath(this.appData.runtime, MIGRATION_JOURNAL_FILENAME);
    const tmpPath = `${filePath}.tmp`;
    try {
      await this.fs.writeFile(tmpPath, JSON.stringify(journal, null, 2));
      await this.fs.rename(tmpPath, filePath);
    } catch (cause) {
      throw new MigrationFailure({
        code: MIGRATION_FAILURE_CODES.JOURNAL_FAILED,
        step: journal.failedStep,
        txnId: journal.txnId,
        backupDirPath: journal.backupDirPath,
        recoveryInstruction:
          "Migration bookkeeping could not be persisted. Old data is intact. " +
          "Check app-data permissions, then retry.",
        cause,
      });
    }
  }
}

/** A declared read-only resource tree copied into `data/<destRelPath>`. */
export interface SourceTree {
  /** Absolute path of an immutable (install) resource directory to copy FROM. */
  sourcePath: string;
  /** Destination relative to `data/` (e.g. `uploads`, `storage`). */
  destRelPath: string;
  description: string;
}

/**
 * The built-in `files` step: copies read-only resource trees into app-data and
 * verifies integrity by re-hashing every copied file (post-copy evidence).
 */
export function createFilesCopyStep(
  sources: readonly SourceTree[],
  options: { verifyCopy?: boolean } = {},
): MigrationStep {
  const verifyCopy = options.verifyCopy ?? true;
  return {
    id: "files",
    run: async (ctx: MigrationContext) => {
      for (const source of sources) {
        const files = await walkSourceTree(ctx.fs, source.sourcePath);
        for (const rel of files) {
          const srcAbs = containPath(source.sourcePath, rel);
          const destAbs = containPath(ctx.appData.data, source.destRelPath, rel);
          await ctx.fs.mkdir(containPath(ctx.appData.data, source.destRelPath), {
            recursive: true,
          });
          await ctx.fs.mkdir(path.dirname(destAbs), { recursive: true });
          await ctx.fs.copyFile(srcAbs, destAbs);
          if (verifyCopy) {
            const src = await hashFile(ctx.fs, srcAbs);
            const dest = await hashFile(ctx.fs, destAbs);
            if (src.hash !== dest.hash || src.size !== dest.size) {
              throw new Error(`files step copy integrity mismatch for ${rel} (${source.description})`);
            }
          }
        }
      }
    },
  };
}

/**
 * Adapt a `MigrationRunner` to the runtime's `MigrationGate` contract so the
 * DesktopRuntime state machine consumes `needsMigration()`/`run()` during the
 * `migrating` state (key_links: runtime cannot report ready until committed).
 */
export function migrationGateFrom(runner: MigrationRunner): MigrationGate {
  return {
    needsMigration: () => runner.needsMigration(),
    run: async () => {
      await runner.run();
    },
  };
}

async function walkSourceTree(fs: DataFs, sourceRoot: string): Promise<string[]> {
  const result: string[] = [];
  const stack: string[] = [""];
  while (stack.length > 0) {
    const rel = stack.pop() as string;
    const abs = rel === "" ? sourceRoot : containPath(sourceRoot, rel);
    for (const name of (await fs.readdir(abs)).sort()) {
      const childRel = rel === "" ? name : `${rel}/${name}`;
      const childAbs = containPath(sourceRoot, childRel);
      if ((await fs.stat(childAbs)).isDirectory()) {
        stack.push(childRel);
      } else {
        result.push(childRel);
      }
    }
  }
  return result.sort();
}
