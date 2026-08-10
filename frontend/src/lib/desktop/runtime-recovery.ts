/**
 * Renderer-side runtime recovery source seam (Phase 43, plan 43-04 renderer
 * wiring, D-43-08/D-43-09, T-43-04-02).
 *
 * `RuntimeGate` gates product routes behind an honest `RuntimeRecoveryState`
 * (contract: desktop/src/shared/runtime-status.ts). This module is the
 * data-source seam between the gate and the desktop shell:
 *
 * - Browser mode: no `window.novelMindDesktop` bridge → `desktopRuntimeRecoverySource()`
 *   returns null and the gate degrades to plain children (no gate, no error).
 * - Desktop mode today: the shell bridge exposes only the shell-level
 *   `DesktopRuntimeStatus` (ready/appVersion/electronVersion/security). The
 *   managed-runtime recovery channel (`RuntimeRecoveryState` over the bridge) is
 *   a future main-process wiring point (43-04 SUMMARY). Until it lands, the
 *   default source maps the shell status to a minimal honest state: ready only
 *   when the shell reports ready, never a fabricated failure, and never an
 *   action the bridge cannot execute.
 * - Tests and the future wiring may inject a `RuntimeRecoverySource` directly
 *   to drive full starting/migrating/degraded/failed states and allowlisted
 *   actions.
 *
 * Action execution follows the bridge: only `restart` maps to the shell-level
 * `requestRuntimeRestart` capability today; the remaining allowlist actions
 * resolve once the main-process recovery channel lands. Bounded action IDs are
 * enforced by the shared contract (`RECOVERY_ACTION_IDS` / `isActionAllowed`).
 */
import type { DesktopRuntimeStatus } from "../../../../desktop/src/shared/bridge-contract";
import type {
  RecoveryActionId,
  RuntimeRecoveryState,
} from "../../../../desktop/src/shared/runtime-status";
import { desktopCapabilities } from "./capabilities";

/** Result of requesting a bounded recovery action. */
export type RecoveryRequestResult = { ok: true } | { ok: false; error: string };

/**
 * Push/pull source of `RuntimeRecoveryState` for the renderer gate. `request`
 * asks the desktop authority to execute a bounded, allowlisted recovery action;
 * the main process re-checks the allowlist before executing anything
 * (T-43-04-02 — the renderer can never force a free-form action).
 */
export interface RuntimeRecoverySource {
  /** Current renderer-safe recovery state (pull). */
  getStatus(): Promise<RuntimeRecoveryState>;
  /** Push updates; returns an unsubscribe handle. */
  subscribe(listener: (state: RuntimeRecoveryState) => void): () => void;
  /** Request a bounded recovery action. */
  request(actionId: RecoveryActionId): Promise<RecoveryRequestResult>;
}

/**
 * Default recovery source. Returns null in browser mode (no bridge) so the gate
 * degrades without rendering anything; otherwise a shell-status-backed source
 * (honest minimal state, no fabricated actions).
 */
export function desktopRuntimeRecoverySource(): RuntimeRecoverySource | null {
  if (!desktopCapabilities.isDesktop) return null;
  return shellRecoverySource();
}

/**
 * Minimal honest state derived from the shell bridge status. While the managed
 * runtime recovery channel is not wired, `ready` mirrors the shell load state
 * and `recoveryActions` is always empty — the gate never shows buttons for
 * actions the bridge cannot execute (T-43-04-02).
 */
function deriveShellState(status: DesktopRuntimeStatus): RuntimeRecoveryState {
  const ready = status.ready;
  return {
    state: ready ? "ready" : "starting",
    ready,
    failedComponent: null,
    errorCode: null,
    errorMessage: null,
    recoveryActions: [],
    backupAvailable: false,
    startedAt: null,
  };
}

function shellRecoverySource(): RuntimeRecoverySource {
  return {
    async getStatus(): Promise<RuntimeRecoveryState> {
      const capability = await desktopCapabilities.getRuntimeStatus();
      if (!capability.supported) {
        // Unreachable after `isDesktop` passed; degrade to a non-ready shell state.
        return {
          state: "starting",
          ready: false,
          failedComponent: null,
          errorCode: null,
          errorMessage: null,
          recoveryActions: [],
          backupAvailable: false,
          startedAt: null,
        };
      }
      return deriveShellState(capability.value);
    },

    subscribe(listener: (state: RuntimeRecoveryState) => void): () => void {
      const subscription = desktopCapabilities.onRuntimeStatus((status) => {
        listener(deriveShellState(status));
      });
      // `onRuntimeStatus` returns null only when the bridge is absent, which
      // cannot happen here (the source is only constructed for a desktop shell).
      return () => subscription?.unsubscribe();
    },

    async request(actionId: RecoveryActionId): Promise<RecoveryRequestResult> {
      // Only the shell-level restart capability exists on today's bridge; the
      // remaining bounded actions resolve once the recovery channel lands.
      if (actionId === "restart") {
        const result = await desktopCapabilities.requestRuntimeRestart();
        if (result.supported && result.value.ok) return { ok: true };
        if (result.supported && !result.value.ok) {
          return { ok: false, error: result.value.reason };
        }
        return { ok: false, error: "bridge-unavailable" };
      }
      return { ok: false, error: `action ${actionId} is not wired to the desktop shell yet` };
    },
  };
}
