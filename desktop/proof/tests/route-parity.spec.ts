/**
 * Phase 41 route-parity proof (Plan 41-02): the 13 frozen renderer routes must serve,
 * hydrate and navigate through the Next standalone server on a loopback port — with no
 * backend running.
 *
 * Inventory contract:
 * - The frozen inventory (route-inventory.json) is loaded and the discovered page routes
 *   derived from frontend/src/app at suite start MUST equal the expected count of 13.
 *   A count mismatch FAILS the suite (no padding) per D-41-02 / D-41-06.
 *
 * Per-route assertions:
 * - HTTP 200 for the route.
 * - Hydrated static markers and/or deterministic error-state test ids (backend absent).
 * - Critical static assets reachable from the standalone tree (JS chunks, public).
 *
 * Navigation:
 * - At least one client navigation transition per route group via the app-shell sidebar.
 *
 * Backend absence is intentional: the 5 dynamic pages are "use client" and pull from the
 * API, which is not running here. API requests are intercepted in the browser context —
 * `/api/auth/me` returns the fixture user so the app shell renders; all other `/api` and
 * `/agent` calls return 404 so dynamic pages land on their deterministic error state.
 * Parity therefore asserts route 200 + static assets + hydration + client navigation,
 * never backend data.
 */

import { test, expect, type Page } from "@playwright/test";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startNextStandalone } from "../src/next-server.ts";

const SPEC_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SPEC_DIR, "..", "..", "..");
const APP_DIR = path.join(REPO_ROOT, "frontend", "src", "app");

interface InventoryRoute {
  path: string;
  params: Record<string, string>;
  markers: string[];
  testIds: string[];
}
interface InventoryGroup {
  id: string;
  label: string;
  routes: InventoryRoute[];
}
interface Inventory {
  expectedRouteCount: number;
  fixture: { novelId: string; keySceneSetId: string };
  groups: InventoryGroup[];
}

const inventory: Inventory = JSON.parse(
  readFileSync(path.join(SPEC_DIR, "route-inventory.json"), "utf8"),
) as Inventory;

/** Derives the current page route paths from frontend/src/app (sorted for determinism). */
function discoverAppRoutes(): string[] {
  const found: string[] = [];
  const walk = (dir: string, prefix: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
      a.name.localeCompare(b.name),
    )) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name.startsWith("_") || entry.name.startsWith(".")) continue;
        walk(full, `${prefix}/${entry.name}`);
      } else if (entry.name === "page.tsx" || entry.name === "page.ts") {
        found.push(prefix === "" ? "/" : prefix);
      }
    }
  };
  walk(APP_DIR, "");
  return found;
}

/** Renders an inventory path template with its fixture params (e.g. /novels/[id] -> /novels/1). */
function concretePath(route: InventoryRoute): string {
  let out = route.path;
  for (const [key, value] of Object.entries(route.params)) {
    out = out.replace(`[${key}]`, value);
  }
  return out;
}

const ALL_ROUTES = inventory.groups.flatMap((group) => group.routes);
const DISCOVERED = discoverAppRoutes();

test.describe("route inventory contract", () => {
  test(`exactly ${inventory.expectedRouteCount} routes frozen — no padding, no drift`, () => {
    expect(DISCOVERED).toHaveLength(inventory.expectedRouteCount);
    expect(ALL_ROUTES).toHaveLength(inventory.expectedRouteCount);

    const discoveredSet = new Set(DISCOVERED);
    for (const route of ALL_ROUTES) {
      expect(discoveredSet.has(route.path), `route ${route.path} not discovered in frontend/src/app`).toBe(true);
    }
    for (const found of DISCOVERED) {
      const frozen = ALL_ROUTES.some((r) => r.path === found);
      expect(frozen, `route ${found} discovered but missing from frozen inventory`).toBe(true);
    }
  });
});

test.describe("Next standalone route parity", () => {
  let baseUrl: string;
  let server: Awaited<ReturnType<typeof startNextStandalone>>;

  test.beforeAll(async () => {
    server = await startNextStandalone({
      endpoint: { host: "127.0.0.1", port: 0 },
      resourceRoot: REPO_ROOT,
      executable: { kind: "electron-embedded-node", path: "server.js" },
    });
    baseUrl = server.baseUrl;
  });

  test.afterAll(async () => {
    await server.stop();
  });

  test.describe.configure({ mode: "serial" });

  // Each test gets a fresh browser context (default). Route requests are intercepted so
  // the app shell renders with the fixture user and dynamic pages show deterministic
  // error states instead of depending on the absent backend.
  test.beforeEach(async ({ context }) => {
    await context.route(/\/api\//, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: 1,
            username: "proof",
            email: "proof@example.com",
            is_active: true,
          }),
        });
        return;
      }
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
    });
    await context.route(/\/agent\//, async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
    });
  });

  /** Asserts the route serves with HTTP 200, its markers are hydrated and its error test-ids resolve. */
  async function assertRouteServed(page: Page, route: InventoryRoute): Promise<void> {
    const url = `${baseUrl}${concretePath(route)}`;
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
    // App shell nav present = AuthGate passed = hydration ran client-side.
    await expect(page.getByLabel("主导航"), `app shell hydrated on ${url}`).toBeAttached();
  }

  test("static assets are served from the standalone tree", async ({ request }) => {
    // Public assets copied into the standalone tree.
    expect((await request.get(`${baseUrl}/icons/icon-192.png`)).status()).toBe(200);
    expect((await request.get(`${baseUrl}/sw.js`)).status()).toBe(200);
    // Next static JS chunks referenced by the root HTML are served.
    const root = await request.get(`${baseUrl}/`);
    expect(root.status()).toBe(200);
    const html = await root.text();
    const scriptSrcs = [...html.matchAll(/src="(\/_next\/static\/[^"]+)"/g)].map((m) => m[1]);
    expect(scriptSrcs.length, "root page must reference at least one _next/static chunk").toBeGreaterThan(0);
    for (const src of scriptSrcs) {
      const asset = await request.get(`${baseUrl}${src}`);
      expect(asset.status(), `static chunk ${src}`).toBe(200);
    }
  });

  for (const route of ALL_ROUTES) {
    test(`route serves and hydrates: ${route.path}`, async ({ page }) => {
      await assertRouteServed(page, route);
    });
  }

  // At least one client navigation transition per route group via the app-shell sidebar.
  for (const group of inventory.groups) {
    test(`client navigation from group "${group.id}"`, async ({ page }) => {
      const source = group.routes[0]!;
      await assertRouteServed(page, source);

      // Pick a sidebar destination; sidebar labels are 工作台/书架/分析/评测/创作/设置中心.
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

      await page.waitForURL(`${baseUrl}${target.dest}`);
      await expect(page.getByLabel("主导航")).toBeAttached();
      expect(new URL(page.url()).pathname).toBe(target.dest);
    });
  }
});
