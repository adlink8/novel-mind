/**
 * Shell smoke suite (Phase 42, Plan 42-01).
 *
 * Launches the production-shaped Electron shell against the existing Next
 * standalone renderer (started by globalSetup on a dynamic loopback port via
 * the Electron-embedded Node) and asserts the security boundary with
 * privilege-negative tests:
 * - the window loads the existing app;
 * - every security flag is on (sandbox, contextIsolation, no nodeIntegration,
 *   webSecurity);
 * - the bridge exposes exactly the four named capabilities and nothing else
 *   (no generic send/invoke/on, no fs/shell/env/process);
 * - the renderer cannot reach require, Node filesystem/shell/env, process or
 *   the raw ipcRenderer;
 * - popups and external navigation are blocked;
 * - browser mode has no window.novelMindDesktop and still renders.
 */
import { test, expect, _electron as electron, chromium } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";
import {
  DESKTOP_BRIDGE_KEY,
  type DesktopBridge,
  type DesktopRuntimeStatus,
} from "../src/shared/bridge-contract";

const DESKTOP_DIR = path.resolve(__dirname, "..");
const LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "::1"];

let electronApp: ElectronApplication;
let page: Page;

test.beforeAll(async () => {
  const rendererUrl = process.env.NOVELMIND_SMOKE_RENDERER_URL;
  if (!rendererUrl) {
    throw new Error(
      "NOVELMIND_SMOKE_RENDERER_URL is not set — run via playwright (globalSetup provides it)",
    );
  }
  electronApp = await electron.launch({
    cwd: DESKTOP_DIR,
    args: ["."],
    env: { ...process.env, NOVELMIND_RENDERER_URL: rendererUrl },
  });
  page = await electronApp.firstWindow();
  await page.waitForLoadState("domcontentloaded");
});

test.afterAll(async () => {
  await electronApp?.close();
});

test("loads the existing renderer in the shell window", async () => {
  await expect.poll(() => page.title()).toContain("NovelMind");
  const bodyLength = await page.evaluate(() => document.body.textContent?.length ?? 0);
  expect(bodyLength).toBeGreaterThan(0);
});

test("exposes exactly the four bridge capabilities and nothing else", async () => {
  const keys = await page.evaluate(
    (key) => {
      const bridge = (window as unknown as Record<string, unknown>)[key] as Record<string, unknown>;
      return Object.keys(bridge ?? {}).sort();
    },
    DESKTOP_BRIDGE_KEY,
  );
  expect(keys).toEqual([
    "getBootstrap",
    "getRuntimeStatus",
    "onRuntimeStatus",
    "requestRuntimeRestart",
  ]);
});

test("window flags are secure (sandbox, contextIsolation, no nodeIntegration, webSecurity)", async () => {
  // Main-side: the shell window was created by our factory and loads only the
  // approved loopback origin (require is not available in the serialized eval
  // context, so the flag values are asserted via the bridge below — the bridge
  // reads them from the main-process posture registry over real IPC).
  const mainSideUrl = await electronApp.evaluate(({ BrowserWindow }) => {
    const win = BrowserWindow.getAllWindows()[0];
    if (win === undefined) throw new Error("no main window");
    return win.webContents.getURL();
  });
  expect(LOOPBACK_HOSTS).toContain(new URL(mainSideUrl).hostname);

  // Bridge-reported posture must agree with the live main-process registry.
  await expect
    .poll(async () => {
      const status = await page.evaluate(async (key) => {
        const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
          DesktopBridge,
          "getRuntimeStatus"
        >;
        return bridge.getRuntimeStatus();
      }, DESKTOP_BRIDGE_KEY);
      return status.ready;
    })
    .toBe(true);

  const status: DesktopRuntimeStatus = await page.evaluate(async (key) => {
    const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
      DesktopBridge,
      "getRuntimeStatus"
    >;
    return bridge.getRuntimeStatus();
  }, DESKTOP_BRIDGE_KEY);
  expect(status.appVersion).toBe("0.1.0");
  expect(status.electronVersion.length).toBeGreaterThan(0);
  expect(status.security).toEqual({
    sandbox: true,
    contextIsolation: true,
    nodeIntegration: false,
    webSecurity: true,
  });
});

test("renderer cannot reach require, Node fs/shell, env, process or raw ipcRenderer", async () => {
  const probe = await page.evaluate(() => {
    const g = globalThis as Record<string, unknown>;
    const win = window as unknown as Record<string, unknown>;
    let requireCallThrew = false;
    try {
      (g as unknown as { require: (id: string) => unknown }).require("fs");
    } catch {
      requireCallThrew = true;
    }
    return {
      requireType: typeof g.require,
      processType: typeof g.process,
      moduleType: typeof g.module,
      ipcRendererType: typeof win.ipcRenderer,
      requireCallThrew,
    };
  });
  expect(probe).toEqual({
    requireType: "undefined",
    processType: "undefined",
    moduleType: "undefined",
    ipcRendererType: "undefined",
    requireCallThrew: true,
  });
});

test("getBootstrap returns a minimal payload with no secrets, env, or paths", async () => {
  const bootstrap = await page.evaluate(async (key) => {
    const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
      DesktopBridge,
      "getBootstrap"
    >;
    return bridge.getBootstrap();
  }, DESKTOP_BRIDGE_KEY);
  expect(bootstrap).toEqual({ appVersion: "0.1.0", bridgeVersion: 1, features: ["desktop-shell"] });
  expect(Object.keys(bootstrap).sort()).toEqual(["appVersion", "bridgeVersion", "features"]);
});

test("onRuntimeStatus subscribes and unsubscribes cleanly", async () => {
  const hasUnsubscribe = await page.evaluate((key) => {
    const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
      DesktopBridge,
      "onRuntimeStatus"
    >;
    const sub = bridge.onRuntimeStatus(() => {});
    const result = typeof sub.unsubscribe === "function";
    sub.unsubscribe();
    return result;
  }, DESKTOP_BRIDGE_KEY);
  expect(hasUnsubscribe).toBe(true);
});

test("popups and external navigation are blocked", async () => {
  const popupNull = await page.evaluate(() => window.open("https://example.com") === null);
  expect(popupNull).toBe(true);

  await page.evaluate(() => {
    window.location.href = "https://example.com";
  });
  await page.waitForTimeout(500);
  const currentUrl = await page.evaluate(() => window.location.href);
  expect(LOOPBACK_HOSTS).toContain(new URL(currentUrl).hostname);
});

test("browser mode: no desktop bridge, renderer still renders (web compatibility)", async () => {
  const rendererUrl = process.env.NOVELMIND_SMOKE_RENDERER_URL;
  if (!rendererUrl) {
    throw new Error("NOVELMIND_SMOKE_RENDERER_URL is not set");
  }

  let executablePath: string;
  try {
    executablePath = chromium.executablePath();
  } catch {
    test.skip(true, "playwright chromium not installed — skipping browser-mode check");
    return;
  }
  if (!existsSync(executablePath)) {
    test.skip(true, "playwright chromium not installed — skipping browser-mode check");
    return;
  }

  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext();
    const browserPage = await ctx.newPage();
    await browserPage.goto(rendererUrl, { waitUntil: "domcontentloaded" });
    const hasBridge = await browserPage.evaluate(
      (key) => key in window,
      DESKTOP_BRIDGE_KEY,
    );
    expect(hasBridge).toBe(false);
    const bodyLength = await browserPage.evaluate(() => document.body.textContent?.length ?? 0);
    expect(bodyLength).toBeGreaterThan(0);
    expect(await browserPage.title()).toContain("NovelMind");
  } finally {
    await browser.close();
  }
});
