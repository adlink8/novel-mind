/**
 * Main-process BrowserWindow factory (D-42-01 / D-42-02 / D-42-03).
 *
 * Every production window is created here with the security baseline:
 * `contextIsolation`, `sandbox`, no node integration, `webSecurity` enabled,
 * and it only ever loads the approved local loopback app origin. Unapproved
 * navigation, redirects, popups, webviews, downloads and permission requests
 * are denied by default (D-42-03). The production CSP is injected on the
 * session at window creation (D-42-07).
 */
import { BrowserWindow } from "electron";
import path from "node:path";
import type { DesktopSecurityPosture } from "../shared/bridge-contract";
import { isApprovedAppUrl } from "./security/approved-origin";
import { applyCspToSession } from "./security/csp";
import type { CspMode } from "./security/csp";
import { applyNavigationPolicy } from "./security/navigation";
import { applyPermissionPolicy } from "./security/permissions";

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
 * (`127.0.0.1`, `localhost`, `[::1]`) on any port. Delegates to the shared
 * security/approved-origin predicate.
 */
export { isApprovedAppUrl } from "./security/approved-origin";

export interface CreateMainWindowOptions {
  rendererUrl: string;
  cspMode?: CspMode;
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
    icon: path.join(process.cwd(), "assets", "novelmind-icon.png"),
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

  // Deny-by-default permission policy (D-42-03).
  applyPermissionPolicy(ses);

  // Popups, webviews, navigation, redirects and downloads denied by default (D-42-03).
  applyNavigationPolicy(win);

  // Production is the fail-closed default. The local launcher explicitly opts
  // into the React development CSP; packaged builds never do.
  applyCspToSession(ses, opts.cspMode ?? "production");

  void win.loadURL(opts.rendererUrl);
  return win;
}
