/**
 * NovelMind desktop main process (D-42-01).
 *
 * Owns application lifecycle and window creation; holds the ONLY privileged
 * capabilities in the app. Every IPC handler validates the sender against the
 * main window and its frame origin before doing anything (D-42-05), and
 * rejects deterministically on malformed/unknown requests.
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
import { app, BrowserWindow, ipcMain } from "electron";
import type { IpcMainInvokeEvent } from "electron";
import {
  DESKTOP_IPC_CHANNELS,
  type DesktopBootstrap,
  type DesktopRuntimeStatus,
  type RestartRequestResult,
} from "../shared/bridge-contract";
import { createMainWindow, isApprovedAppUrl, securityPostureFor } from "./create-window";

/** Dev default until Phase 43 wires the local service orchestrator. */
const DEV_RENDERER_DEFAULT = "http://127.0.0.1:3000";

let mainWindow: BrowserWindow | null = null;
let shellReady = false;

function resolveRendererUrl(): string {
  const raw = process.env.NOVELMIND_RENDERER_URL;
  if (raw === undefined || raw === "") return DEV_RENDERER_DEFAULT;
  if (!isApprovedAppUrl(raw)) {
    throw new Error(`NOVELMIND_RENDERER_URL must be a loopback http origin (got "${raw}")`);
  }
  return raw;
}

/** D-42-05: reject any IPC that is not from the main window's main frame. */
function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const win = mainWindow;
  if (win === null || win.isDestroyed()) {
    throw new Error("desktop shell is not ready");
  }
  if (event.sender !== win.webContents) {
    throw new Error("IPC sender is not the main window");
  }
  const frameUrl = event.senderFrame?.url;
  if (frameUrl === undefined || !isApprovedAppUrl(frameUrl)) {
    throw new Error("IPC sender frame origin is not approved");
  }
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

function registerIpcHandlers(): void {
  ipcMain.handle(DESKTOP_IPC_CHANNELS.getRuntimeStatus, (event): DesktopRuntimeStatus => {
    assertTrustedSender(event);
    return currentStatus();
  });

  ipcMain.handle(DESKTOP_IPC_CHANNELS.getBootstrap, (event): DesktopBootstrap => {
    assertTrustedSender(event);
    return {
      appVersion: app.getVersion(),
      bridgeVersion: 1,
      features: ["desktop-shell"],
    };
  });

  ipcMain.handle(
    DESKTOP_IPC_CHANNELS.requestRuntimeRestart,
    (event): RestartRequestResult => {
      assertTrustedSender(event);
      if (!shellReady) return { ok: false, reason: "not-ready" };
      app.relaunch();
      app.exit(0);
      return { ok: true };
    },
  );
}

app.whenReady().then(() => {
  const rendererUrl = resolveRendererUrl();
  mainWindow = createMainWindow({ rendererUrl });
  registerIpcHandlers();

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
      mainWindow = createMainWindow({ rendererUrl });
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
