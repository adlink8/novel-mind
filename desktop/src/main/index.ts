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
import { app, BrowserWindow } from "electron";
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
import { nodeProcessOperations } from "../runtime/process-operations";
import { RUNTIME_ERROR_CODES, RuntimeError } from "../runtime/types";

/** Dev default until the managed runtime resolves the renderer. */
const DEV_RENDERER_DEFAULT = "http://127.0.0.1:3000";

let mainWindow: BrowserWindow | null = null;
let shellReady = false;
let resolvedRendererUrl: string | null = null;
/** The runtime instance this process created (owned for shutdown). */
let ownedRuntime: DesktopRuntime | null = null;

/**
 * The local service orchestrator behind ONE runtime interface (D-43-02). Wave 1
 * (plan 43-02) wires the development adapter so the graph and readiness are
 * exercised end-to-end; the packaged adapter replaces it when the bundled
 * runtimes land in a later plan.
 */
function runtimeInstance(): DesktopRuntime {
  if (ownedRuntime === null) {
    ownedRuntime = new DesktopRuntime({
      adapter: new DevelopmentProcessAdapter(nodeProcessOperations()),
    });
  }
  return ownedRuntime;
}

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

  [DESKTOP_IPC_CHANNELS.getBootstrap]: (): DesktopBootstrap => ({
    appVersion: app.getVersion(),
    bridgeVersion: 1,
    features: ["desktop-shell"],
  }),

  [DESKTOP_IPC_CHANNELS.requestRuntimeRestart]: (): RestartRequestResult => {
    if (!shellReady) return { ok: false, reason: "not-ready" };
    app.relaunch();
    app.exit(0);
    return { ok: true };
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
