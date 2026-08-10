/**
 * Main-process BrowserWindow factory (D-42-01 / D-42-02 / D-42-03).
 *
 * Every production window is created here with the security baseline:
 * `contextIsolation`, `sandbox`, no node integration, `webSecurity` enabled,
 * and it only ever loads the approved local loopback app origin. Unapproved
 * navigation, redirects, popups, webviews, downloads and permission requests
 * are denied by default (D-42-03).
 */
import { BrowserWindow } from "electron";
import path from "node:path";
import type { DesktopSecurityPosture } from "../shared/bridge-contract";

/**
 * The security baseline every production window is created with (D-42-02).
 * Keep in sync with the webPreferences object below; the shell-smoke suite
 * asserts every flag end-to-end.
 */
const SECURE_POSTURE: DesktopSecurityPosture = {
  sandbox: true,
  contextIsolation: true,
  nodeIntegration: false,
  webSecurity: true,
};

/**
 * Windows are only created through createMainWindow, so this registry is the
 * authoritative record of each window's effective security posture. Electron's
 * `getLastWebPreferences` is absent from the Electron 43 public typings, so we
 * own the truth instead of probing an untyped runtime API.
 */
const securityPostureByWindow = new Map<number, DesktopSecurityPosture>();

/** Returns the registered security posture for a window created by this module. */
export function securityPostureFor(win: BrowserWindow): DesktopSecurityPosture {
  const posture = securityPostureByWindow.get(win.id);
  if (posture === undefined) {
    throw new Error(`window ${win.id} has no registered security posture`);
  }
  return posture;
}

/**
 * The only origin a production window may load or navigate to: loopback HTTP
 * (`127.0.0.1`, `localhost`, `[::1]`) on any port. Ports stay dynamic until
 * Phase 43 pins the packaged origin (D-41-05 dynamic-port contract).
 */
export function isApprovedAppUrl(raw: string): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== "http:") return false;
  const host = url.hostname;
  return host === "127.0.0.1" || host === "localhost" || host === "::1";
}

export interface CreateMainWindowOptions {
  rendererUrl: string;
}

export function createMainWindow(opts: CreateMainWindowOptions): BrowserWindow {
  if (!isApprovedAppUrl(opts.rendererUrl)) {
    throw new Error(
      `refusing to load unapproved renderer URL "${opts.rendererUrl}" — only loopback http origins are allowed`,
    );
  }

  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    title: "NovelMind",
    show: false,
    backgroundColor: "#0a0a0a",
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "index.js"),
      ...SECURE_POSTURE,
      nodeIntegrationInWorker: false,
      allowRunningInsecureContent: false,
    },
  });

  securityPostureByWindow.set(win.id, SECURE_POSTURE);
  win.once("closed", () => {
    securityPostureByWindow.delete(win.id);
  });

  win.once("ready-to-show", () => win.show());

  const ses = win.webContents.session;

  // No permission requests (camera/mic/geolocation/notifications/...) are granted.
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);

  // Popups / new windows are denied (D-42-03).
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));

  // <webview> embedding is denied.
  win.webContents.on("will-attach-webview", (event) => event.preventDefault());

  // Hard navigation and redirects are only allowed within the approved loopback origin.
  win.webContents.on("will-navigate", (event, targetUrl) => {
    if (!isApprovedAppUrl(targetUrl)) event.preventDefault();
  });
  win.webContents.on("will-redirect", (event, targetUrl) => {
    if (!isApprovedAppUrl(targetUrl)) event.preventDefault();
  });

  // Downloads are denied (D-42-03).
  ses.on("will-download", (event) => event.preventDefault());

  void win.loadURL(opts.rendererUrl);
  return win;
}
