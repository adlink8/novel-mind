/**
 * Critical workflow smoke inside the Electron shell (Phase 42, Plan 42-03,
 * Task 2 / T-42-03-02).
 *
 * With the backend absent, the API surface is mocked the same way the Phase 41
 * browser proof did — but asserted INSIDE the real Electron window:
 * - `/api/auth/me` returns 401 until a login has happened, so the login page is
 *   the deterministic landing surface;
 * - POST `/api/auth/login` succeeds and flips the mock to authenticated, so a
 *   real form submit drives the app shell (主导航 + the six sidebar entries).
 *
 * The Next standalone renderer is started by globalSetup; the shell window
 * loads it via NOVELMIND_SMOKE_RENDERER_URL.
 */
import { test, expect } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import { launchShell } from "./launch";

/** Same fixture user the Phase 41 browser proof used (route-inventory.json). */
const FIXTURE_USER = {
  id: 1,
  username: "proof",
  email: "proof@example.com",
  is_active: true,
};

/** Sidebar entries rendered by the app shell once authenticated. */
const NAV_ITEMS = ["工作台", "书架", "分析", "评测", "创作", "设置中心"];

let electronApp: ElectronApplication;
let page: Page;
let rendererUrl: string;
let loggedIn = false;

test.beforeAll(async () => {
  const envUrl = process.env.NOVELMIND_SMOKE_RENDERER_URL;
  if (!envUrl) {
    throw new Error(
      "NOVELMIND_SMOKE_RENDERER_URL is not set — run via playwright (globalSetup provides it)",
    );
  }
  rendererUrl = envUrl;
  // 45-03: packaged exe when NOVELMIND_PACKAGED_EXE is set, else dev Electron.
  electronApp = await launchShell();

  const context = electronApp.context();
  // Catch-all registered first so the specific mocks below take precedence.
  await context.route(/\/api\//, async (route) => {
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });
  await context.route(/\/api\/auth\/me/, async (route) => {
    await route.fulfill(
      loggedIn
        ? { status: 200, contentType: "application/json", body: JSON.stringify(FIXTURE_USER) }
        : { status: 401, contentType: "application/json", body: "{}" },
    );
  });
  await context.route(/\/api\/auth\/login/, async (route) => {
    loggedIn = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        access_token: "proof-token",
        token_type: "bearer",
        user_id: 1,
        username: "proof",
      }),
    });
  });

  page = await electronApp.firstWindow();
  await page.waitForLoadState("domcontentloaded");
});

test.afterAll(async () => {
  await electronApp?.close();
});

test.describe.configure({ mode: "serial" });

test("login page is reachable inside the Electron window", async () => {
  await page.goto(`${rendererUrl}/`, { waitUntil: "domcontentloaded" });
  await expect(page.getByText("回到你的故事里")).toBeAttached();
  await expect(page.getByLabel("用户名")).toBeAttached();
  await expect(page.getByLabel("密码")).toBeAttached();
  await expect(page.getByRole("button", { name: "登录" })).toBeAttached();
});

test("login submit renders the main navigation", async () => {
  await page.getByLabel("用户名").fill("proof");
  await page.getByLabel("密码").fill("password123");
  await page.getByRole("button", { name: "登录" }).click();

  // The login POST flips the auth/me mock, so the shell renders after submit.
  await expect(page.getByLabel("主导航")).toBeAttached();
  for (const label of NAV_ITEMS) {
    await expect(page.getByTitle(label).first()).toBeAttached();
  }
});
