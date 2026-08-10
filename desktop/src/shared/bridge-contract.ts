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
 * - Exactly five capabilities, one typed method per capability. No generic
 *   send/invoke/on surface, no filesystem, shell, environment or process
 *   objects are ever exposed.
 * - The bootstrap payload carries no secrets, paths or process details.
 */
import type { RuntimeBootstrap } from "./bootstrap-contract";
import type { RedactedCredentialStatus } from "./credential-status";

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
  openExternalLink: "bridge:openExternalLink",
  getLocalAuthToken: "bridge:getLocalAuthToken",
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
 * Startup payload for the renderer. Explicitly NO env, NO process details,
 * NO absolute paths (T-42-01-02). `runtime` carries the one-session bootstrap
 * (44-01): dynamically allocated loopback endpoints and bounded session
 * metadata only — never secrets, provider keys or process paths — and stays
 * null until the managed runtime is fully ready (D-43-09). `credentials`
 * carries ONLY the redacted credential status (44-02/44-03) — provider/local-auth
 * state strings, never any value or blob fragment (T-44-02-01).
 */
export interface DesktopBootstrap {
  appVersion: string;
  bridgeVersion: 1;
  features: readonly string[];
  /** One-session runtime bootstrap, or null until the runtime is ready. */
  runtime: RuntimeBootstrap | null;
  /** Redacted OS-credential status: state strings only, never values. */
  credentials: RedactedCredentialStatus;
}

/** Result of a restart request. */
export type RestartRequestResult =
  | { ok: true }
  | { ok: false; reason: "not-ready" | "denied" };

/**
 * Result of an explicit external-link request. The failure payload carries a
 * stable redacted code — never a URL, path or environment detail.
 */
export type OpenExternalLinkResult =
  | { ok: true }
  | { ok: false; code: string; reason: string };

/** Subscription handle returned by `onRuntimeStatus`. */
export interface RuntimeStatusSubscription {
  unsubscribe: () => void;
}

export type RuntimeStatusListener = (status: DesktopRuntimeStatus) => void;

/**
 * The two local services that accept desktop session tokens. The renderer may
 * request a token for either service; the token is audience-bound, short-lived
 * and session-scoped (44-02 local-auth contract).
 */
export const LOCAL_AUTH_TARGETS = ["backend", "agent"] as const;
export type LocalAuthTarget = (typeof LOCAL_AUTH_TARGETS)[number];

/**
 * The complete capability surface the renderer may use.
 *
 * `getLocalAuthToken` (44-03) hands the renderer the SHORT-LIVED, audience-bound
 * session token it needs to authenticate the SSE/HTTP transport to the local
 * managed services. It is never a master credential: the HMAC secret, provider
 * keys and the credential store stay main-owned, and the token expires in
 * minutes and is only valid on the loopback service of its audience.
 */
export interface DesktopBridge {
  /** Current runtime status (one-shot pull). */
  getRuntimeStatus(): Promise<DesktopRuntimeStatus>;
  /** Ask the main process to restart the desktop runtime. */
  requestRuntimeRestart(): Promise<RestartRequestResult>;
  /** Minimal startup payload for the renderer. */
  getBootstrap(): Promise<DesktopBootstrap>;
  /** Open a validated external HTTPS link in the OS default browser. */
  openExternalLink(url: string): Promise<OpenExternalLinkResult>;
  /** Subscribe to runtime status changes; returns an unsubscribe handle. */
  onRuntimeStatus(listener: RuntimeStatusListener): RuntimeStatusSubscription;
  /**
   * Short-lived audience-bound local session token for the requested service,
   * or null when no active runtime session exists (fail closed). Never the HMAC
   * secret and never a provider key.
   */
  getLocalAuthToken(target: LocalAuthTarget): Promise<string | null>;
}
