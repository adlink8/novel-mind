/**
 * NovelMind desktop main process (D-42-01).
 *
 * Owns application lifecycle and window creation; holds the ONLY privileged
 * capabilities in the app. Every IPC handler is registered through
 * `registerBridgeIpcHandlers`, which validates the sender (webContents/frame/
 * origin), the channel, payload size and payload shape before any capability
 * logic runs, and rejects deterministically on malformed/unknown requests
 * (D-42-05 / T-42-02-01 / T-42-02-03).
 *
 * The renderer URL comes from NOVELMIND_RENDERER_URL and is validated against
 * the approved loopback origin in createMainWindow. Phase 43 replaces the env
 * injection with the local service orchestrator.
 *
 * Task 1 gate note: Phase 41-03 recorded NO-GO (honest record preserved).
 * User-authorized override `gate_overrides.phase_42_45_execution` in
 * `.planning/config.json` authorizes 42-45 execution; prerequisite #1
 * (bundled Node via ELECTRON_RUN_AS_NODE) is proven in
 * `desktop/proof/bundled-node-evidence.json`.
 */
import { app, BrowserWindow, safeStorage } from "electron";
import type { IpcMainInvokeEvent } from "electron";
import {
  DESKTOP_IPC_CHANNELS,
  type DesktopBootstrap,
  type DesktopRuntimeStatus,
  type OpenExternalLinkResult,
  type RestartRequestResult,
} from "../shared/bridge-contract";
import { createMainWindow, isApprovedAppUrl, securityPostureFor } from "./create-window";
import { openExternalLink } from "./security/navigation";
import {
  registerBridgeIpcHandlers,
  unregisterBridgeIpcHandlers,
  type BridgeHandler,
} from "./ipc/register";
import { DesktopRuntime } from "../runtime/desktop-runtime";
import { DevelopmentProcessAdapter } from "../runtime/development-process-adapter";
import { RuntimeBootstrapProvider } from "../runtime/bootstrap";
import { nodeProcessOperations } from "../runtime/process-operations";
import { RUNTIME_ERROR_CODES, RuntimeError } from "../runtime/types";
import { DesktopLocalAuth } from "../security/local-auth";
import { CredentialStore, type SafeStorage } from "../security/credential-store";
import { buildAppDataPaths, nodeDataFs } from "../data/app-data-layout";
import { enforceSingleInstance } from "./single-instance";
import { randomBytes } from "node:crypto";
import path from "node:path";

/** Dev default until the managed runtime resolves the renderer. */
const DEV_RENDERER_DEFAULT = "http://127.0.0.1:3000";

let mainWindow: BrowserWindow | null = null;
let shellReady = false;
let resolvedRendererUrl: string | null = null;
/** The runtime instance this process created (owned for shutdown). */
let ownedRuntime: DesktopRuntime | null = null;
/** One-session bootstrap producer bound to the owned runtime (44-01). */
let bootstrapProvider: RuntimeBootstrapProvider | null = null;
/**
 * Main-owned local session auth (44-02). Tokens are audience/expiry/session
 * bound; the HMAC secret is injected into owned process environments and never
 * leaves main (T-44-02-01). Rotates on runtime restart so a prior-session token
 * is rejected (T-44-02-03).
 */
let localAuth: DesktopLocalAuth | null = null;
/**
 * Lazily-created OS-protected credential store (44-02/44-03). Encrypted blobs live
 * only under the app-data secrets root; the renderer receives ONLY the redacted
 * status (provider/local-auth state strings), never a value (T-44-02-01).
 * Lazily created after `app` ready (safeStorage requires it).
 */
let credentialStore: CredentialStore | null = null;

/**
 * Single-instance enforcement (45-01, D-45-02 / T-45-01-02) runs at module load,
 * BEFORE any runtime graph or window exists. The lock is scoped to the app's
 * userData directory, so a duplicate launch (same `%APPDATA%/NovelMind`) routes
 * its intent to the existing window and exits without starting a second runtime
 * graph.
 *
 * Test seam: NOVELMIND_USER_DATA overrides the userData root (mirrors
 * `defaultAppDataRoot` in app-data-layout.ts) so the process-behavior suite can
 * run deterministic isolated instances. It is set before the lock is requested,
 * so the lock is correctly scoped to the overridden root.
 */
const userDataOverride = process.env.NOVELMIND_USER_DATA;
if (userDataOverride !== undefined && userDataOverride !== "") {
  app.setPath("userData", path.resolve(userDataOverride));
}

const singleInstance = enforceSingleInstance({ getMainWindow: () => mainWindow });
if (!singleInstance.isPrimary) {
  // A NovelMind instance is already running — exit immediately, no runtime, no window.
  app.exit(0);
}

/**
 * The local service orchestrator behind ONE runtime interface (D-43-02). Wave 1
 * (plan 43-02) wires the development adapter so the graph and readiness are
 * exercised end-to-end; the packaged adapter replaces it when the bundled
 * runtimes land in a later plan.
 */
function runtimeInstance(): DesktopRuntime {
  if (ownedRuntime === null) {
    ownedRuntime = new DesktopRuntime({
      // The adapter injects the main-owned local-auth HMAC secret into the
      // agent service environment at spawn (44-03): with a secret configured the
      // agent service enforces audience/expiry-bound local session tokens on
      // every inbound run request. The backend middleware stays opt-in in this
      // wave (injecting there would 401 the renderer's user-JWT calls and the
      // readiness probe) — see 44-03-SUMMARY deviation.
      adapter: new DevelopmentProcessAdapter(nodeProcessOperations(), undefined, {
        repoRoot: process.cwd(),
        localAuthSecret: () => localAuthSecret(),
      }),
    });
  }
  return ownedRuntime;
}

/** Lazily-created session bootstrap producer (44-01). */
function sessionBootstrapProvider(): RuntimeBootstrapProvider {
  if (bootstrapProvider === null) {
    bootstrapProvider = new RuntimeBootstrapProvider({ runtime: () => ownedRuntime });
  }
  return bootstrapProvider;
}

/**
 * Lazily-created local session auth (44-02). Tokens are bound to the current
 * runtime bootstrap session id (null → no active session → tokens() fails
 * closed). The HMAC secret is handed to the owned process adapter so the agent
 * service verifies the audience/expiry-bound tokens.
 */
function localAuthInstance(): DesktopLocalAuth {
  if (localAuth === null) {
    localAuth = new DesktopLocalAuth({
      sessionId: () => sessionBootstrapProvider().currentSessionId(),
    });
  }
  return localAuth;
}

/** The current local-auth HMAC secret, or null when not yet initialized. */
function localAuthSecret(): string | null {
  return localAuth === null ? null : localAuth.secret();
}

/**
 * Lazily-created OS-protected credential store (44-03 wiring). Uses Electron's
 * async `safeStorage` (Windows DPAPI) with a stable session-scoped OS key id;
 * the renderer only ever sees the redacted status.
 */
function credentialStoreInstance(): CredentialStore {
  if (credentialStore === null) {
    const paths = buildAppDataPaths({
      userDataDir: app.getPath("userData"),
      // Packaged mode: the installed (immutable) resources are the directory of
      // the running exe. Passing installRoot makes the layout constructor fail
      // closed if app-data ever overlaps the installation (D-45-03, D-43-05).
      installRoot: app.isPackaged ? path.dirname(process.execPath) : undefined,
    });
    const storage: SafeStorage = {
      isEncryptionAvailable: () => safeStorage.isEncryptionAvailable(),
      currentKeyId: () => credentialKeyNonce,
      encryptString: (plainText: string) => safeStorage.encryptString(plainText),
      decryptString: (encrypted: Buffer) => safeStorage.decryptString(encrypted),
    };
    credentialStore = new CredentialStore({ paths, fs: nodeDataFs(), safeStorage: storage });
  }
  return credentialStore;
}

/**
 * Per-process nonce identifying the safeStorage key in effect this session.
 * A blob written in a prior app session is detected as `rotation_needed`
 * (see credential-store SafeStorage contract).
 */
const credentialKeyNonce = randomBytes(16).toString("hex");

function resolveRendererUrl(raw: string): string {
  if (raw === undefined || raw === "") return DEV_RENDERER_DEFAULT;
  if (!isApprovedAppUrl(raw)) {
    throw new Error(`NOVELMIND_RENDERER_URL must be a loopback http origin (got "${raw}")`);
  }
  return raw;
}

/**
 * Resolves the renderer URL through the managed runtime when no explicit
 * override is given. The env override is kept for hermetic shell tests and
 * explicit dev tunnels; otherwise the next endpoint comes from the runtime
 * snapshot — never a hard-coded port, never ready-with-a-failed-dependency.
 */
async function ensureRendererUrl(): Promise<string> {
  const explicit = process.env.NOVELMIND_RENDERER_URL;
  if (explicit !== undefined && explicit !== "") {
    return resolveRendererUrl(explicit);
  }
  const snapshot = await runtimeInstance().ensureReady();
  const next = snapshot.components.find((c) => c.id === "next");
  if (snapshot.ready && next !== undefined && next.ready && next.endpoint !== null) {
    const url = `http://${next.endpoint.host}:${next.endpoint.port}`;
    if (isApprovedAppUrl(url)) return url;
  }
  // Never render the shell against an unready runtime (D-43-09).
  throw new RuntimeError(
    RUNTIME_ERROR_CODES.READY_INVARIANT_VIOLATION,
    "renderer requires a fully ready runtime graph",
  );
}

function currentSecurityPosture(): DesktopRuntimeStatus["security"] {
  const win = mainWindow;
  if (win === null || win.isDestroyed()) {
    return { sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true };
  }
  return securityPostureFor(win);
}

function currentStatus(): DesktopRuntimeStatus {
  const electronVersion = (process.versions as { electron?: string }).electron ?? "unknown";
  return {
    ready: shellReady,
    appVersion: app.getVersion(),
    electronVersion,
    security: currentSecurityPosture(),
  };
}

function broadcastRuntimeStatus(): void {
  const win = mainWindow;
  if (win === null || win.isDestroyed()) return;
  win.webContents.send(DESKTOP_IPC_CHANNELS.runtimeStatusChanged, currentStatus());
}

function mainWindowProvider(): BrowserWindow | null {
  return mainWindow;
}

const capabilityHandlers: Record<string, BridgeHandler> = {
  [DESKTOP_IPC_CHANNELS.getRuntimeStatus]: (): DesktopRuntimeStatus => currentStatus(),

  [DESKTOP_IPC_CHANNELS.getBootstrap]: async (): Promise<DesktopBootstrap> => ({
    appVersion: app.getVersion(),
    bridgeVersion: 1,
    features: ["desktop-shell"],
    runtime: await sessionBootstrapProvider().get(),
    credentials: await credentialStoreInstance().status(),
  }),

  [DESKTOP_IPC_CHANNELS.requestRuntimeRestart]: (): RestartRequestResult => {
    if (!shellReady) return { ok: false, reason: "not-ready" };
    // Invalidate the current session bootstrap: a relaunched runtime is a new
    // session (44-01 restart invalidation).
    bootstrapProvider?.invalidate();
    app.relaunch();
    app.exit(0);
    return { ok: true };
  },

  // 44-03 local-session token bridge: the renderer asks for a SHORT-LIVED,
  // audience-bound token for the local agent service (the only consumer in this
  // wave). Null is returned when no active runtime session exists — fail closed,
  // never a token minted for a session-less runtime. The HMAC secret and the
  // credential store stay main-owned; the renderer holds only this expiring
  // session token, never a master credential (D-44-02/D-44-03).
  [DESKTOP_IPC_CHANNELS.getLocalAuthToken]: (
    _event: IpcMainInvokeEvent,
    ...args: unknown[]
  ): string | null => {
    const target = args[0];
    if (target !== "backend" && target !== "agent") {
      // Unknown target (compromised renderer): fail closed with the typed
      // session-less result rather than minting for an unspecified service.
      return null;
    }
    const tokens = localAuthInstance().tokens();
    if (tokens === null) return null;
    return target === "agent" ? tokens.agent : tokens.backend;
  },

  // Explicit external-link capability: the URL is validated main-side (HTTPS
  // only, no credentials) before `shell.openExternal` — the renderer never
  // contributes shell arguments or flags (D-42-03). Schema validation in the
  // registration layer guarantees args[0] is a bounded string before dispatch.
  [DESKTOP_IPC_CHANNELS.openExternalLink]: (
    _event: IpcMainInvokeEvent,
    ...args: unknown[]
  ): Promise<OpenExternalLinkResult> => openExternalLink(typeof args[0] === "string" ? args[0] : ""),
};

app.whenReady().then(async () => {
  const rendererUrl = await ensureRendererUrl();
  resolvedRendererUrl = rendererUrl;
  mainWindow = createMainWindow({ rendererUrl });

  // D-42-05: all bridge IPC flows through the sender/schema-validating
  // registration point; handlers are removed on quit (T-42-02-03).
  registerBridgeIpcHandlers(mainWindowProvider, capabilityHandlers);

  mainWindow.webContents.on("did-finish-load", () => {
    shellReady = true;
    broadcastRuntimeStatus();
  });
  mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription) => {
    console.error(
      `[desktop] renderer failed to load ${rendererUrl}: ${errorCode} ${errorDescription}`,
    );
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createMainWindow({ rendererUrl: resolvedRendererUrl ?? rendererUrl });
    }
  });
});

app.on("will-quit", () => {
  unregisterBridgeIpcHandlers();
  // A quitting runtime must never serve a session bootstrap again (44-01).
  bootstrapProvider?.invalidate();
  // Owned runtime shutdown is best-effort on quit (D-43-07): never block exit
  // on a slow drain, but give the owned tree a chance to terminate cleanly.
  if (ownedRuntime !== null) {
    void ownedRuntime.shutdown().catch(() => undefined);
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
