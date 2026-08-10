/**
 * DesktopBridge contract — the single typed surface between the Electron
 * main/preload process and the web-compatible renderer.
 *
 * This module is PURE (no Electron and no Node imports) so it can be shared
 * across the trust boundary:
 * - preload: `import type` only — erased at compile time, keeping the sandboxed
 *   preload self-contained (a sandboxed preload cannot `require` local modules
 *   at runtime).
 * - renderer (frontend) code: type-only import — never pulled into the web
 *   bundle, so the Next renderer stays fully browser-compatible.
 * - main process: full import — the main process is unsandboxed.
 *
 * Security contract (T-42-01-01 / T-42-01-02):
 * - Exactly four capabilities, one typed method per capability. No generic
 *   send/invoke/on surface, no filesystem, shell, environment or process
 *   objects are ever exposed.
 * - The bootstrap payload carries no secrets, paths or process details.
 */

/** Global key under which the bridge is exposed in the renderer main world. */
export const DESKTOP_BRIDGE_KEY = "novelMindDesktop";

/**
 * IPC channel names used between preload and main.
 *
 * Single source of truth for the main process. The sandboxed preload inlines
 * these strings (it cannot require this module at runtime); keep both in sync —
 * the shell smoke suite catches drift end-to-end.
 */
export const DESKTOP_IPC_CHANNELS = {
  getRuntimeStatus: "bridge:getRuntimeStatus",
  requestRuntimeRestart: "bridge:requestRuntimeRestart",
  getBootstrap: "bridge:getBootstrap",
  runtimeStatusChanged: "bridge:runtimeStatusChanged",
} as const;

/** Effective BrowserWindow security posture reported to the renderer. */
export interface DesktopSecurityPosture {
  sandbox: boolean;
  contextIsolation: boolean;
  nodeIntegration: boolean;
  webSecurity: boolean;
}

/** One-shot lifecycle status. Never contains secrets, paths or env. */
export interface DesktopRuntimeStatus {
  /** True once the shell finished loading the renderer and is fully usable. */
  ready: boolean;
  /** Desktop app semantic version, e.g. "0.1.0". */
  appVersion: string;
  /** Electron runtime version string, e.g. "43.3.0". */
  electronVersion: string;
  security: DesktopSecurityPosture;
}

/**
 * Minimal startup payload for the renderer. Explicitly NO env, NO process
 * details, NO absolute paths (T-42-01-02).
 */
export interface DesktopBootstrap {
  appVersion: string;
  bridgeVersion: 1;
  features: readonly string[];
}

/** Result of a restart request. */
export type RestartRequestResult =
  | { ok: true }
  | { ok: false; reason: "not-ready" | "denied" };

/** Subscription handle returned by `onRuntimeStatus`. */
export interface RuntimeStatusSubscription {
  unsubscribe: () => void;
}

export type RuntimeStatusListener = (status: DesktopRuntimeStatus) => void;

/** The complete capability surface the renderer may use. */
export interface DesktopBridge {
  /** Current runtime status (one-shot pull). */
  getRuntimeStatus(): Promise<DesktopRuntimeStatus>;
  /** Ask the main process to restart the desktop runtime. */
  requestRuntimeRestart(): Promise<RestartRequestResult>;
  /** Minimal startup payload for the renderer. */
  getBootstrap(): Promise<DesktopBootstrap>;
  /** Subscribe to runtime status changes; returns an unsubscribe handle. */
  onRuntimeStatus(listener: RuntimeStatusListener): RuntimeStatusSubscription;
}
