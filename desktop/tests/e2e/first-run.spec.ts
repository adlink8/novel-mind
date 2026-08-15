/**
 * First-run qualification against the SHIPPED packaged exe (Phase 45, plan
 * 45-03, Task 2 / D-45-01 / D-45-02).
 *
 * Proves on the packaged artifact (win-unpacked/NovelMind.exe when
 * NOVELMIND_PACKAGED_EXE is set):
 *  - the app starts a real window titled NovelMind and the shell hydrates
 *    against the bundled renderer (HTTP 200 root + app shell markers);
 *  - exactly ONE window exists and NO extra console/GUI process is created
 *    (single-instance + no-console, D-45-02);
 *  - the packaged exe is a GUI-subsystem binary (PE Subsystem 2 — never pops a
 *    console);
 *  - the desktop bridge is present (proves the preload+shared contract loaded
 *    in the packaged window).
 *
 * The renderer is served from the bundled next-standalone tree through the
 * packaged exe's embedded Node (ELECTRON_RUN_AS_NODE) at a dynamic loopback
 * port — the same mechanism the packaged runtime adapter uses.
 */
import { test, expect } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import { launchShell, packagedExePath } from "./launch";
import { peSubsystem } from "../package/pe-subsystem";

let electronApp: ElectronApplication;
let page: Page;
let rendererUrl: string;

test.beforeAll(async () => {
  const envUrl = process.env.NOVELMIND_SMOKE_RENDERER_URL;
  if (!envUrl) {
    throw new Error(
      "NOVELMIND_SMOKE_RENDERER_URL is not set — run via playwright (globalSetup provides it)",
    );
  }
  rendererUrl = envUrl;
  electronApp = await launchShell();
  page = await electronApp.firstWindow();
  await page.waitForLoadState("domcontentloaded");
});

test.afterAll(async () => {
  await electronApp?.close();
});

test.describe("packaged first-run readiness", () => {
  test("exactly one window, titled NovelMind, renderer serves HTTP 200", async () => {
    const winCount = await electronApp!.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().length,
    );
    expect(winCount).toBe(1);
    await expect.poll(() => page.title()).toContain("NovelMind");
    const status = await page.evaluate(async (url) => (await fetch(url)).status, `${rendererUrl}/`);
    expect(status).toBe(200);
  });

  test("the app shell hydrates against the bundled renderer", async () => {
    // The login gate renders once AuthGate resolves; the shell markers prove
    // the React tree hydrated client-side in the packaged window.
    await page.goto(`${rendererUrl}/`, { waitUntil: "domcontentloaded" });
    await expect(page.getByText("回到你的故事里")).toBeAttached();
    await expect(page.getByLabel("用户名")).toBeAttached();
    await expect(page.getByLabel("密码")).toBeAttached();
  });

  test("packaged exe is a GUI-subsystem binary (no console window)", () => {
    const exe = packagedExePath();
    expect(exe, "NOVELMIND_PACKAGED_EXE must be set for the packaged first-run suite").not.toBeNull();
    expect(peSubsystem(exe!)).toBe("gui");
  });

  test("desktop bridge is exposed (preload + shared contract loaded)", async () => {
    const surface = await page.evaluate(() => {
      const bridge = (window as unknown as Record<string, unknown>)["novelMindDesktop"];
      if (typeof bridge !== "object" || bridge === null) return null;
      return Object.keys(bridge as Record<string, unknown>).sort();
    });
    expect(surface).toEqual([
      "getBootstrap",
      "getLocalAuthToken",
      "getRuntimeStatus",
      "onRuntimeStatus",
      "openExternalLink",
      "requestRuntimeRestart",
    ]);
  });
});
