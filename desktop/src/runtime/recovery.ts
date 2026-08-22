/**
 * Bounded runtime recovery executor (Phase 43, plan 43-04, D-43-08/D-43-09,
 * T-43-04-02).
 *
 * `RuntimeRecovery` is the main-process authority that turns a renderer
 * recovery request into a typed lifecycle operation. It never trusts the
 * renderer:
 * - An action is executed only when it appears in the state-derived allowlist
 *   (`recoveryActionIdsFor`) — anything else is denied with the stable
 *   `RECOVERY_DENIED` code.
 * - Every failure is redacted to a stable code; `restoreBackup` failures map to
 *   `BACKUP_RESTORE_FAILED` (old data stays intact).
 * - The backup-restore capability is injected (the desktop runtime is not a
 *   data authority, D-43-04); when absent, `restoreBackup` is never offered and
 *   `backupAvailable` is always false.
 *
 * This module imports nothing from the backend domain and nothing from the data
 * module: recovery data capabilities arrive through the `RecoveryDataCapabilities`
 * seam (adapted from the MigrationRunner by the main wiring), keeping the
 * runtime authority boundary intact.
 */
import {
  isRecoveryActionId,
  recoveryActionIdsFor,
  runtimeStatusFromSnapshot,
  type RecoveryActionId,
  type RuntimeRecoveryState,
} from "../shared/runtime-status";
import {
  RUNTIME_ERROR_CODES,
  RuntimeError,
  type RedactedError,
  type RuntimeComponent,
  type RuntimeSnapshot,
} from "./types";
import type { DesktopRuntime } from "./desktop-runtime";

/** Injected data-recovery capability (main wiring adapts a MigrationRunner). */
export interface RecoveryDataCapabilities {
  /** True when a verified pre-migration backup exists. */
  backupAvailable(): Promise<boolean>;
  /** Bounded recovery instruction from the last failed migration, or null. */
  recoveryInstruction(): Promise<string | null>;
  /** Restore the pre-migration backup over data/ (old data preserved). */
  restoreBackup(): Promise<void>;
}

export interface RuntimeRecoveryOptions {
  runtime: DesktopRuntime;
  /** Injected data capabilities; omit when no migration data is managed. */
  data?: RecoveryDataCapabilities;
}

export interface RecoveryResult {
  status: RuntimeRecoveryState;
  /** True when the action was applied successfully. */
  applied: boolean;
  /** Set when the action was denied or failed (bounded redacted payload). */
  error: RedactedError | null;
  /** Present for the `openDiagnostics` action. */
  diagnostics?: DiagnosticsHandle;
}

/** Redacted diagnostic handle — component labels only, never paths (T-42-01-02). */
export interface DiagnosticsHandle {
  opened: true;
  /** Components that are not stopped (their log sinks may exist). */
  sinks: readonly RuntimeComponent[];
  /** Fixed note; never an absolute path or environment detail. */
  note: string;
}

function toRedacted(cause: unknown): RedactedError {
  if (cause instanceof RuntimeError) return cause.redacted();
  return { code: RUNTIME_ERROR_CODES.INTERNAL, message: "unexpected runtime error" };
}

export class RuntimeRecovery {
  private readonly runtime: DesktopRuntime;
  private readonly data: RecoveryDataCapabilities | null;

  constructor(options: RuntimeRecoveryOptions) {
    this.runtime = options.runtime;
    this.data = options.data ?? null;
  }

  /** Current renderer-safe recovery status. */
  async status(): Promise<RuntimeRecoveryState> {
    const snapshot = await this.runtime.status();
    return runtimeStatusFromSnapshot(snapshot, {
      backupAvailable: await this.backupAvailable(),
    });
  }

  /** Redacted diagnostic handle — no paths, no secrets, no command lines. */
  async diagnostics(): Promise<DiagnosticsHandle> {
    const snapshot = await this.runtime.status();
    return {
      opened: true,
      sinks: snapshot.components.filter((c) => c.state !== "stopped").map((c) => c.id),
      note: "diagnostic logs live under the app-data logs/ directory",
    };
  }

  /** Bounded recovery instruction from the last failed migration, or null. */
  async migrationRecoveryInstruction(): Promise<string | null> {
    if (this.data === null) return null;
    try {
      return await this.data.recoveryInstruction();
    } catch {
      return null;
    }
  }

  /**
   * Validate and apply a renderer recovery request. Unknown identifiers and
   * actions not allowed in the current state are denied with RECOVERY_DENIED;
   * the executed lifecycle operation never bypasses the runtime state machine.
   */
  async recover(raw: unknown): Promise<RecoveryResult> {
    if (!isRecoveryActionId(raw)) {
      return {
        status: await this.status(),
        applied: false,
        error: {
          code: RUNTIME_ERROR_CODES.RECOVERY_DENIED,
          message: "unknown recovery action",
        },
      };
    }
    return this.apply(raw);
  }

  private async apply(actionId: RecoveryActionId): Promise<RecoveryResult> {
    const snapshot = await this.runtime.status();
    const backupAvailable = await this.backupAvailable();
    const status = (): Promise<RuntimeRecoveryState> =>
      Promise.resolve(runtimeStatusFromSnapshot(snapshot, { backupAvailable }));

    if (!recoveryActionIdsFor(snapshot.state, backupAvailable).includes(actionId)) {
      return {
        status: await status(),
        applied: false,
        error: {
          code: RUNTIME_ERROR_CODES.RECOVERY_DENIED,
          message: `action ${actionId} is not allowed while runtime is ${snapshot.state}`,
        },
      };
    }

    try {
      switch (actionId) {
        case "retry":
          // From stopped/failed: full start; from degraded: repair.
          await this.runtime.ensureReady();
          break;
        case "restart":
          await this.runtime.restart(this.failedComponentOf(snapshot));
          break;
        case "openDiagnostics":
          return {
            status: await status(),
            applied: true,
            error: null,
            diagnostics: await this.diagnostics(),
          };
        case "restoreBackup": {
          const data = this.requireData();
          try {
            await data.restoreBackup();
          } catch (cause) {
            return {
              status: await status(),
              applied: false,
              error: {
                code: RUNTIME_ERROR_CODES.BACKUP_RESTORE_FAILED,
                message: "backup restore failed; old data remains intact",
              },
            };
          }
          break;
        }
      }
      return { status: await this.status(), applied: true, error: null };
    } catch (cause) {
      return {
        status: await this.status(),
        applied: false,
        error: toRedacted(cause),
      };
    }
  }

  /** Restart targets the failed component (and its dependents); undefined = whole graph. */
  private failedComponentOf(snapshot: RuntimeSnapshot): RuntimeComponent | undefined {
    return snapshot.components.find((c) => c.state === "failed")?.id;
  }

  private requireData(): RecoveryDataCapabilities {
    if (this.data === null) {
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.RECOVERY_DENIED,
        "no backup restore capability is available",
      );
    }
    return this.data;
  }

  /** Availability is advisory; restore itself still validates the evidence. */
  private async backupAvailable(): Promise<boolean> {
    if (this.data === null) return false;
    try {
      return await this.data.backupAvailable();
    } catch {
      return false;
    }
  }
}
