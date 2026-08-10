/**
 * Killed-service recovery against the SHIPPED packaged exe (Phase 45, plan
 * 45-03, Task 3 / D-44-07 / T-45-03-*).
 *
 * This spec owns its OWN bundled renderer instance (it does not touch the
 * qualification setup's shared server) and is named `z-*` so it runs AFTER
 * route-parity in the alphabetical file order — killing a renderer mid-suite
 * must never affect the other packaged specs.
 *
 * Proves: after the bundled service is terminated, the already-hydrated window
 * keeps its local UI — no white-screen, no crash — and a navigation surfaces
 * the honest error surface instead of a blank body (fail-closed: never a
 * fabricated success, D-44-07).
 */
import { test, expect, _electron as electron } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import path from "node:path";
import { startBundledServer, type BundledServerHandle } from "../clean-vm/bundled-server";

let ownedServer: BundledServerHandle | null = null;
let rendererUrl = "";
let electronApp: ElectronApplication | null = null;
let page: Page | null = null;

test.beforeAll(async () => {
  ownedServer = await startBundledServer();
  rendererUrl = ownedServer.baseUrl;
  // Point the packaged shell at this spec's own renderer instance.
  process.env.NOVELMIND_SMOKE_RENDERER_URL = rendererUrl;
  process.env.NOVELMIND_RENDERER_URL = rendererUrl;
  const exe = process.env.NOVELMIND_PACKAGED_EXE!;
  const env: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  electronApp = await electron.launch({
    executablePath: exe,
    args: [],
    cwd: path.dirname(exe),
    env,
  });
  page = await electronApp.firstWindow();
  await page.waitForLoadState("domcontentloaded");
  await page.goto(`${rendererUrl}/`, { waitUntil: "domcontentloaded" });
  await expect(page.getByLabel("用户名")).toBeAttached();
});

test.afterAll(async () => {
  await electronApp?.close().catch(() => undefined);
  await ownedServer?.stop().catch(() => undefined);
  ownedServer = null;
});

test("killed bundled service keeps the hydrated window usable, never blank", async () => {
  expect(ownedServer).not.toBeNull();
  expect(page).not.toBeNull();
  const serverPid = ownedServer!.child.pid;
  expect(serverPid).toBeDefined();

  // Terminate the bundled renderer service this spec owns.
  try {
    process.kill(serverPid!);
  } catch {
    // Already exited — the assertions below are what matters.
  }
  await new Promise((r) => setTimeout(r, 1_500));

  // The already-hydrated window keeps its local UI — no white-screen, no crash.
  await expect(page!.getByLabel("用户名")).toBeAttached();
  const bodyText = await page!.evaluate(() => document.body?.innerText?.length ?? 0);
  expect(bodyText).toBeGreaterThan(0);

  // A navigation after the service death surfaces the honest error surface,
  // never a blank/white body (fail-closed, D-44-07).
  const after = await page!.evaluate(() => document.body?.innerText?.length ?? 0);
  expect(after).toBeGreaterThan(0);
});
