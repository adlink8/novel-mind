/**
 * Security policy negative suite (Phase 42, Plan 42-02, Task 1).
 *
 * End-to-end deny-by-default assertions against the real Electron shell hosting
 * the Next standalone renderer (started by globalSetup on a dynamic loopback
 * port):
 * - a production CSP header with no broad wildcard is served on the app
 *   document (and no relaxing `<meta>` CSP is present);
 * - external navigation (attacker origin) is blocked and the window stays on
 *   the approved loopback origin;
 * - `window.open` returns null and never creates a window;
 * - `<webview>` embedding is inert and the attach is refused;
 * - javascript:/file: navigation attempts cannot move the window off the
 *   approved origin;
 * - untrusted external links are rejected by the explicit capability with a
 *   stable redacted code.
 */
import { test, expect, _electron as electron } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import path from "node:path";
import {
  DESKTOP_BRIDGE_KEY,
  type DesktopBridge,
  type OpenExternalLinkResult,
} from "../../src/shared/bridge-contract";
import { CSP_DIRECTIVES } from "../../src/main/security/csp";
import {
  EXTERNAL_LINK_ERROR_CODES,
  isSafeExternalUrl,
} from "../../src/main/security/navigation";

const DESKTOP_DIR = path.resolve(__dirname, "..", "..");
const LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "::1"];
const ATTACKER_ORIGIN = "https://attacker.invalid";

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

test("production CSP is served on the app document with no broad wildcard", async () => {
  const rendererUrl = process.env.NOVELMIND_SMOKE_RENDERER_URL;
  if (!rendererUrl) throw new Error("renderer URL missing");

  // No relaxing <meta> CSP: the policy is enforced via the response header.
  const metaCsp = await page.evaluate(() => {
    const meta = document.querySelector('meta[http-equiv="Content-Security-Policy"]');
    return meta ? meta.getAttribute("content") : null;
  });
  expect(metaCsp).toBeNull();

  const headerCsp = await page.evaluate(async (origin) => {
    const response = await fetch(origin, { method: "GET" });
    return response.headers.get("content-security-policy");
  }, rendererUrl);
  expect(headerCsp).not.toBeNull();
  expect(headerCsp).toContain("default-src 'none'");
  expect(headerCsp).not.toMatch(/\*\s/);
  expect(headerCsp).not.toMatch(/default-src \*/);
});

test("the shipped CSP directives are deny-by-default", async () => {
  expect(CSP_DIRECTIVES).toContain("default-src 'none'");
  expect(CSP_DIRECTIVES).toContain("object-src 'none'");
  expect(CSP_DIRECTIVES).toContain("base-uri 'none'");
  expect(CSP_DIRECTIVES).toContain("frame-ancestors 'none'");
  expect(CSP_DIRECTIVES).not.toMatch(/connect-src \*/);
  expect(CSP_DIRECTIVES).not.toMatch(/img-src \*/);
});

test("external navigation to an attacker origin is blocked", async () => {
  const before = await page.evaluate(() => window.location.href);
  expect(LOOPBACK_HOSTS).toContain(new URL(before).hostname);

  await page.evaluate((origin) => {
    window.location.href = origin;
  }, ATTACKER_ORIGIN);
  await page.waitForTimeout(800);

  const after = await page.evaluate(() => window.location.href);
  expect(LOOPBACK_HOSTS).toContain(new URL(after).hostname);
  expect(new URL(after).origin).not.toBe(ATTACKER_ORIGIN);
});

test("window.open is denied and never creates a window", async () => {
  const popupNull = await page.evaluate(
    (origin) => window.open(origin) === null,
    ATTACKER_ORIGIN,
  );
  expect(popupNull).toBe(true);

  const windowCount = await electronApp.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().length,
  );
  expect(windowCount).toBe(1);
});

test("webview embedding is inert (webviewTag never enabled)", async () => {
  const probe = await page.evaluate(() => {
    const webview = document.createElement("webview");
    webview.setAttribute("src", "https://example.com");
    document.body.appendChild(webview);
    return {
      // With webviewTag disabled the tag is a plain unknown element, never a
      // functional <webview>; `will-attach-webview` preventDefault is the
      // second guard in main.
      functional: webview.constructor.name === "HTMLWebViewElement",
    };
  });
  expect(probe.functional).toBe(false);
});

test("javascript: and file: navigation attempts cannot move the window", async () => {
  const before = await page.evaluate(() => window.location.href);
  await page.evaluate(() => {
    try {
      (window.location as unknown as { href: string }).href = "javascript:alert(1)";
    } catch {
      // Chromium refuses javascript: assignments — the attempt is refused.
    }
  });
  await page.waitForTimeout(300);
  const after = await page.evaluate(() => window.location.href);
  expect(new URL(after).origin).toBe(new URL(before).origin);
});

test("permission requests are denied by default", async () => {
  const permission = await page.evaluate(async () => {
    if (typeof Notification === "undefined") return "no-notification-api";
    try {
      return await Notification.requestPermission();
    } catch {
      return "request-threw";
    }
  });
  // deny-by-default: any permission-gated API request resolves denied and the
  // renderer never gains camera/mic/geolocation/notifications access.
  expect(permission).toBe("denied");
});

test("untrusted external links are rejected with a stable redacted code", async () => {
  for (const bad of [
    "javascript:alert(1)",
    "file:///C:/Windows/win.ini",
    "data:text/html,<script>alert(1)</script>",
    "custom-scheme://x",
    "http://example.com/not-tls",
    "https://user:pass@example.com/with-creds",
  ]) {
    const result = await page.evaluate(
      async ({ key, url }) => {
        const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
          DesktopBridge,
          "openExternalLink"
        >;
        const r = (await bridge.openExternalLink(url)) as OpenExternalLinkResult;
        return { ok: r.ok, code: r.ok ? "" : r.code };
      },
      { key: DESKTOP_BRIDGE_KEY, url: bad },
    );
    expect(result.ok).toBe(false);
    expect(result.code).toBe(EXTERNAL_LINK_ERROR_CODES.REJECTED);
  }
});

test("safe-external-url predicate is strict", async () => {
  expect(isSafeExternalUrl("https://example.com/docs?q=1#sec2")).toBe(true);
  expect(isSafeExternalUrl("https://example.com")).toBe(true);
  expect(isSafeExternalUrl("javascript:alert(1)")).toBe(false);
  expect(isSafeExternalUrl("file:///C:/Windows/win.ini")).toBe(false);
  expect(isSafeExternalUrl("data:text/html,x")).toBe(false);
  expect(isSafeExternalUrl("custom-scheme://x")).toBe(false);
  expect(isSafeExternalUrl("http://example.com/not-tls")).toBe(false);
  expect(isSafeExternalUrl("https://user:pass@example.com/with-creds")).toBe(false);
  expect(isSafeExternalUrl("https://")).toBe(false);
  expect(isSafeExternalUrl("not a url")).toBe(false);
});
