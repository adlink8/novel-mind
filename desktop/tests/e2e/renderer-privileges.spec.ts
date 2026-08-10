/**
 * Renderer-privilege negative suite inside the Electron shell (Phase 42, Plan
 * 42-03, Task 2 / T-42-03-01).
 *
 * Proves the renderer surface inside the production window did NOT grow with
 * route parity work: exactly the five declared bridge capabilities are exposed
 * and nothing else, Node globals (require/process/module) and the raw
 * ipcRenderer are unreachable, and popup / off-origin navigation attempts stay
 * denied. This is the E2E negative evidence for T-42-03-01 (Elevation of
 * Privilege -> renderer regressions) mapped to REQ-DESK-01.
 *
 * The Next standalone renderer is started by globalSetup; the shell window
 * loads it via NOVELMIND_SMOKE_RENDERER_URL.
 */
import { test, expect, _electron as electron } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import path from "node:path";
import { DESKTOP_BRIDGE_KEY } from "../../src/shared/bridge-contract";

const DESKTOP_DIR = path.resolve(__dirname, "..", "..");
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

test("the bridge exposes exactly the five declared capabilities and nothing else", async () => {
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
    "openExternalLink",
    "requestRuntimeRestart",
  ]);
});

test("Node globals are unreachable from the renderer (require/process/module/ipcRenderer)", async () => {
  const probe = await page.evaluate(() => {
    const g = globalThis as Record<string, unknown>;
    const win = window as unknown as Record<string, unknown>;
    const attempts: Record<string, string> = {};
    for (const id of ["electron", "fs", "child_process", "path"]) {
      try {
        (g as unknown as { require: (id: string) => unknown }).require(id);
        attempts[id] = "resolved";
      } catch {
        attempts[id] = "threw";
      }
    }
    return {
      requireType: typeof g.require,
      processType: typeof g.process,
      moduleType: typeof g.module,
      ipcRendererType: typeof win.ipcRenderer,
      attempts,
    };
  });
  expect(probe.requireType).toBe("undefined");
  expect(probe.processType).toBe("undefined");
  expect(probe.moduleType).toBe("undefined");
  expect(probe.ipcRendererType).toBe("undefined");
  expect(Object.values(probe.attempts).every((v) => v === "threw")).toBe(true);
});

test("window.open popups are blocked from renderer fixtures", async () => {
  const popupNull = await page.evaluate(
    () => window.open("https://example.com") === null,
  );
  expect(popupNull).toBe(true);

  const windowCount = await electronApp.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().length,
  );
  expect(windowCount).toBe(1);
});

test("external navigation cannot leave the approved loopback origin", async () => {
  const before = await page.evaluate(() => window.location.href);
  expect(LOOPBACK_HOSTS).toContain(new URL(before).hostname);

  await page.evaluate(() => {
    window.location.href = "https://attacker.invalid";
  });
  await page.waitForTimeout(800);

  const after = await page.evaluate(() => window.location.href);
  expect(LOOPBACK_HOSTS).toContain(new URL(after).hostname);
  expect(new URL(after).origin).not.toBe("https://attacker.invalid");
});
