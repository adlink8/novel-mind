/**
 * Runtime recovery status contract (Phase 43, plan 43-04, D-43-08/D-43-09,
 * T-43-04-01/T-43-04-02).
 *
 * PURE module — no Node/Electron imports — so it can cross the main→renderer
 * trust boundary exactly like `bridge-contract.ts`. The renderer gate consumes
 * a `RuntimeRecoveryState` (pulled or pushed over the bridge); the main process
 * derives it from the owned runtime snapshot and authority-checks any requested
 * action before executing it.
 *
 * Contract:
 * - Every failure surfaces a stable redacted `errorCode` (the only stable
 *   signal) plus a bounded, allowlisted set of `recoveryActions`.
 * - `ready` is true ONLY when the runtime state is "ready"; a failed or
 *   degraded runtime can never present itself as an empty domain success
 *   (D-43-09).
 * - The action set is state-derived and bounded (T-43-04-02): the renderer
 *   cannot invent or pass through free-form action strings.
 * - `errorMessage` is always the fixed redacted literal carried by the runtime
 *   snapshot — never a path, environment or command line.
 */
import type {
  RedactedError,
  RuntimeComponent,
  RuntimeSnapshot,
  RuntimeState,
} from "../runtime/types";
import type { RuntimeErrorCode } from "../runtime/types";

/** Exactly the bounded recovery actions the plan names (T-43-04-02 allowlist). */
export const RECOVERY_ACTION_IDS = [
  "retry",
  "restart",
  "openDiagnostics",
  "restoreBackup",
] as const;
export type RecoveryActionId = (typeof RECOVERY_ACTION_IDS)[number];

export function isRecoveryActionId(value: unknown): value is RecoveryActionId {
  return (
    typeof value === "string" && (RECOVERY_ACTION_IDS as readonly string[]).includes(value)
  );
}

export interface RecoveryAction {
  id: RecoveryActionId;
  /** Fixed human label; the renderer may localize it. */
  label: string;
  /** Bounded description of what the action does. */
  description: string;
}

/** Renderer-safe, secret-free runtime recovery state (T-43-04-01). */
export interface RuntimeRecoveryState {
  state: RuntimeState;
  /** True only when the runtime state is "ready" (D-43-09). */
  ready: boolean;
  /** The component that failed, when any. */
  failedComponent: RuntimeComponent | null;
  /** Stable redacted failure code, or null when nothing has failed. */
  errorCode: RuntimeErrorCode | null;
  /** Fixed literal error message, or null. Never a path/secret. */
  errorMessage: string | null;
  /** Bounded actions allowed right now. */
  recoveryActions: readonly RecoveryAction[];
  /** True when a verified pre-migration backup exists for restoreBackup. */
  backupAvailable: boolean;
  /** ISO timestamp of the last successful full startup, or null. */
  startedAt: string | null;
}

const ACTION_LABELS: Record<RecoveryActionId, string> = {
  retry: "Retry",
  restart: "Restart service",
  openDiagnostics: "Open diagnostics",
  restoreBackup: "Restore backup",
};

const ACTION_DESCRIPTIONS: Record<RecoveryActionId, string> = {
  retry: "Start or repair the local runtime",
  restart: "Restart the failed service and its dependents",
  openDiagnostics: "Show redacted diagnostic logs",
  restoreBackup: "Restore the pre-migration backup; old data is intact",
};

/**
 * State-derived action allowlist (T-43-04-02). `backupAvailable` gates the
 * `restoreBackup` action: it is only offered in `failed` when a verified backup
 * exists to restore from.
 */
export function recoveryActionIdsFor(
  state: RuntimeState,
  backupAvailable: boolean,
): readonly RecoveryActionId[] {
  switch (state) {
    case "stopped":
      return ["retry", "openDiagnostics"];
    case "starting":
    case "migrating":
    case "stopping":
      // In-flight: no recovery action is safe (BUSY) — the status alone is honest.
      return [];
    case "ready":
      return []; // nothing to recover
    case "degraded":
      return ["retry", "restart", "openDiagnostics"];
    case "failed":
      return backupAvailable
        ? ["retry", "openDiagnostics", "restoreBackup"]
        : ["retry", "openDiagnostics"];
  }
}

/** Whether `actionId` is currently allowed for the given state. */
export function isActionAllowed(
  state: RuntimeState,
  actionId: RecoveryActionId,
  backupAvailable: boolean,
): boolean {
  return recoveryActionIdsFor(state, backupAvailable).includes(actionId);
}

export function recoveryActionsFor(
  snapshot: RuntimeSnapshot,
  options: { backupAvailable: boolean },
): readonly RecoveryAction[] {
  return recoveryActionIdsFor(snapshot.state, options.backupAvailable).map((id) => ({
    id,
    label: ACTION_LABELS[id],
    description: ACTION_DESCRIPTIONS[id],
  }));
}

/**
 * Derive the renderer-safe recovery state from a runtime snapshot. The failed
 * component is the first component in `failed` state; the error payload is the
 * redacted snapshot error verbatim.
 */
export function runtimeStatusFromSnapshot(
  snapshot: RuntimeSnapshot,
  options: { backupAvailable: boolean },
): RuntimeRecoveryState {
  const failedComponent =
    snapshot.components.find((c) => c.state === "failed")?.id ?? null;
  const error: RedactedError | null = snapshot.lastError;
  return {
    state: snapshot.state,
    ready: snapshot.ready,
    failedComponent,
    errorCode: error?.code ?? null,
    errorMessage: error?.message ?? null,
    recoveryActions: recoveryActionsFor(snapshot, options),
    backupAvailable: options.backupAvailable,
    startedAt: snapshot.startedAt,
  };
}
