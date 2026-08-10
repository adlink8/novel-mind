/**
 * Offline recovery + data preservation against the SHIPPED packaged exe
 * (Phase 45, plan 45-03, Task 3 / D-44-06 / D-44-07 / T-45-03-*).
 *
 * Honest offline behavior on the packaged artifact:
 *  - The packaged renderer is served from the BUNDLED tree via the packaged
 *    exe's embedded Node on a dynamic loopback port — no external network, no
 *    Node/Python/Docker on PATH (the tightened-PATH boundary is enforced by
 *    run-qualification.ps1).
 *  - With the API surface unavailable (mocked 404/401 like the parity suite),
 *    the window NEVER white-screens: the login gate renders and the SPA stays
 *    interactive (local UI works with the backend absent, D-44-06).
 *  - Offline emulation (context.setOffline): `navigator.onLine` flips false and
 *    the already-loaded local UI keeps rendering — no fabricated provider
 *    success.
 *  - Provider capabilities are honestly gated: the packaged first-run has no
 *    provider credentials, so the redacted bootstrap reports provider
 *    "unavailable" (the ProviderCapabilityGate reason 未配置 AI 提供商) instead
 *    of an empty-success artifact (D-44-07).
 *  - Data preservation: with an isolated NOVELMIND_USER_DATA root, a marker
 *    file survives a clean shutdown + relaunch of the packaged exe (D-45-03:
 *    mutable data lives under the app-data root and survives restarts).
 */
import { test, expect } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import { existsSync, writeFileSync, readFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { launchShell } from "./launch";

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

test.describe("offline local content and honest provider gating", () => {
  test("window never white-screens when the API surface is unavailable", async () => {
    // Re-run against a dead-API surface: login gate still renders, no blank body.
    await page.goto(`${rendererUrl}/`, { waitUntil: "domcontentloaded" });
    await expect(page.getByText("回到你的故事里")).toBeAttached();
    await expect(page.getByLabel("用户名")).toBeAttached();
    const bodyText = await page.evaluate(() => document.body?.innerText?.length ?? 0);
    expect(bodyText).toBeGreaterThan(0);
  });

  test("offline emulation keeps local UI interactive (navigator.onLine false)", async () => {
    const context = electronApp!.context();
    await context.setOffline(true);
    await expect(page.getByLabel("用户名")).toBeAttached();
    const online = await page.evaluate(() => navigator.onLine);
    expect(online).toBe(false);
    // The login form remains usable while offline (local render, no network).
    await page.getByLabel("用户名").fill("proof");
    await expect(page.getByLabel("用户名")).toHaveValue("proof");
    await context.setOffline(false);
  });

  test("provider capabilities are honestly gated (no provider configured)", async () => {
    // Redacted bootstrap: packaged first run has no provider credentials, so the
    // capability gate derives "unavailable" (未配置 AI 提供商) — never a
    // fabricated success and never a credential value.
    const credentials = await page.evaluate(async () => {
      const bridge = (window as unknown as Record<string, unknown>)["novelMindDesktop"] as {
        getBootstrap(): Promise<{ credentials: { provider: string } }>;
      };
      const bootstrap = await bridge.getBootstrap();
      return { provider: bootstrap.credentials.provider };
    });
    expect(credentials.provider).toBe("unavailable");
  });
});

test.describe("data preservation across restart (isolated app-data root)", () => {
  test("marker file survives a clean shutdown + relaunch of the packaged exe", async () => {
    const userDataRoot = process.env.NOVELMIND_USER_DATA;
    expect(userDataRoot, "NOVELMIND_USER_DATA must be set for the preservation check").toBeDefined();
    const dataDir = path.join(userDataRoot!, "data");
    mkdirSync(dataDir, { recursive: true });
    const marker = path.join(dataDir, "preservation-marker.txt");
    writeFileSync(marker, "survives-restart", "utf8");
    expect(existsSync(marker)).toBe(true);

    // Clean shutdown of the packaged app (app.quit via window close)…
    await electronApp!.close();
    electronApp = null as unknown as ElectronApplication;

    // …then relaunch with the SAME isolated root and confirm the data is intact.
    const relaunched = await launchShell();
    try {
      const page2 = await relaunched.firstWindow();
      await page2.waitForLoadState("domcontentloaded");
      await expect(page2.getByLabel("用户名")).toBeAttached();
      expect(readFileSync(marker, "utf8")).toBe("survives-restart");
    } finally {
      await relaunched.close();
    }
  });
});
