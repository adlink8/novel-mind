/**
 * Electron in-app route-parity suite (Phase 42, Plan 42-03, Task 2 / T-42-03-02).
 *
 * Proves the 13 frozen renderer routes serve, hydrate and navigate INSIDE the
 * real Electron shell window (launched via `_electron.launch`) against the Next
 * standalone renderer started by globalSetup on a dynamic loopback port — the
 * same inventory (`fixtures/routes.ts` -> `proof/tests/route-inventory.json`)
 * the Phase 41 proof consumed, so Electron parity compares against the identical
 * 13-route surface with no drift.
 *
 * API mocking mirrors the 41-02 browser proof, but inside the Electron window:
 * `/api/auth/me` returns the fixture user so the app shell (主导航) renders; all
 * other `/api` and `/agent` calls return 404 so the 5 dynamic pages land on
 * their deterministic error states. Parity therefore asserts route HTTP 200 +
 * static assets + hydration + client navigation inside the shell — never
 * backend data.
 *
 * T-42-03-01 renderer-privilege negatives live in renderer-privileges.spec.ts.
 */
import { test, expect } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import {
  ALL_ROUTES,
  EXPECTED_ROUTE_COUNT,
  ROUTE_GROUPS,
  concretePath,
  type RouteFixture,
} from "../fixtures/routes";
import { launchShell } from "./launch";

/** Same fixture user the Phase 41 browser proof used (route-inventory.json). */
const FIXTURE_USER = {
  id: 1,
  username: "proof",
  email: "proof@example.com",
  is_active: true,
};

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
  // 45-03: the same spec runs against the SHIPPED packaged exe when
  // NOVELMIND_PACKAGED_EXE is set (win-unpacked/NovelMind.exe); otherwise the
  // dev Electron binary (42-03 behavior).
  electronApp = await launchShell();

  // Context routes are registered BEFORE firstWindow so the initial load and
  // every subsequent navigation see the same deterministic API surface.
  const context = electronApp.context();
  await context.route(/\/api\//, async (route) => {
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });
  await context.route(/\/api\/auth\/me/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FIXTURE_USER),
    });
  });
  await context.route(/\/agent\//, async (route) => {
    await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
  });

  page = await electronApp.firstWindow();
  await page.waitForLoadState("domcontentloaded");
});

test.afterAll(async () => {
  await electronApp?.close();
});

test.describe.configure({ mode: "serial" });

/** Asserts a route serves HTTP 200, its markers/test-ids hydrate and the shell renders. */
async function assertRouteServed(route: RouteFixture): Promise<void> {
  const url = `${rendererUrl}${concretePath(route)}`;
  const response = await page.goto(url, { waitUntil: "domcontentloaded" });
  expect(response?.status(), `HTTP status for ${url}`).toBe(200);

  for (const marker of route.markers) {
    await expect(
      page.getByText(new RegExp(marker)).first(),
      `marker "${marker}" on ${url}`,
    ).toBeAttached();
  }
  for (const testId of route.testIds) {
    await expect(page.getByTestId(testId), `test-id "${testId}" on ${url}`).toBeVisible();
  }
  // App shell nav present = AuthGate passed = hydration ran client-side in the shell window.
  await expect(page.getByLabel("主导航"), `app shell hydrated on ${url}`).toBeAttached();
}

test("frozen inventory contract holds (exactly 13 routes, no drift)", () => {
  expect(ALL_ROUTES).toHaveLength(EXPECTED_ROUTE_COUNT);
  expect(EXPECTED_ROUTE_COUNT).toBe(13);
  const paths = ALL_ROUTES.map((r) => r.path);
  expect(new Set(paths).size).toBe(paths.length);
});

test("static assets are served from the standalone tree inside Electron", async () => {
  const statusOf = (u: string) =>
    page.evaluate(async (url) => (await fetch(url)).status, `${rendererUrl}${u}`);

  // Public assets copied into the standalone tree.
  expect(await statusOf("/icons/icon-192.png")).toBe(200);
  expect(await statusOf("/sw.js")).toBe(200);

  // Next static JS chunks referenced by the root HTML are served.
  const html = await page.evaluate(
    async (url) => (await fetch(url)).text(),
    `${rendererUrl}/`,
  );
  const scriptSrcs = [...html.matchAll(/src="(\/_next\/static\/[^"]+)"/g)]
    .map((m) => m[1])
    .filter((s): s is string => s !== undefined);
  expect(scriptSrcs.length, "root page must reference at least one _next/static chunk").toBeGreaterThan(0);
  for (const src of scriptSrcs) {
    expect(await statusOf(src), `static chunk ${src}`).toBe(200);
  }
});

for (const route of ALL_ROUTES) {
  test(`route serves and hydrates inside Electron: ${route.path}`, async () => {
    await assertRouteServed(route);
  });
}

// At least one client navigation transition per route group via the app-shell sidebar.
for (const group of ROUTE_GROUPS) {
  test(`client navigation from group "${group.id}"`, async () => {
    const source = group.routes[0]!;
    await assertRouteServed(source);

    // Sidebar labels are 工作台/书架/分析/评测/创作/设置中心.
    const navTargets: Array<{ label: string; dest: string }> = [
      { label: "书架", dest: "/novels" },
      { label: "评测", dest: "/eval" },
      { label: "创作", dest: "/writing" },
      { label: "设置中心", dest: "/settings" },
    ];
    // Prefer a destination that is NOT the current route.
    const current = concretePath(source);
    const target =
      navTargets.find((t) => t.dest !== current && !current.startsWith(t.dest + "/")) ??
      navTargets[0]!;

    const nav = page.getByTitle(target.label).first();
    await expect(nav).toBeAttached();
    await nav.click();

    await page.waitForURL(`${rendererUrl}${target.dest}`);
    await expect(page.getByLabel("主导航")).toBeAttached();
    expect(new URL(page.url()).pathname).toBe(target.dest);
  });
}
