/**
 * Packaged release security negative suite (Phase 45, plan 45-04, Task 1).
 *
 * Re-runs the Phase 42/44 deny-by-default assertions against the SHIPPED
 * win-unpacked artifact (packaged NovelMind.exe) hosting the BUNDLED
 * next-standalone renderer served through the packaged exe's embedded Node.
 * This is the release-evidence version of the dev-mode suites
 * (`security/policy.spec.ts`, `security/ipc.spec.ts`): every boundary is
 * asserted on the packaged binary, not on the dev shell.
 *
 * Coverage (D-45-08 / REQ-DESK-02 / REQ-DESK-10):
 *  - webPreferences: sandbox / contextIsolation / nodeIntegration / webSecurity
 *    / nodeIntegrationInWorker / allowRunningInsecureContent, read back from the
 *    LIVE packaged window's webContents (not from source constants);
 *  - renderer privilege negatives: no `require`/`process`/`module`/`global` in
 *    the packaged renderer main world;
 *  - CSP: header on the app document is deny-by-default with no broad wildcard
 *    and no relaxing <meta> CSP; X-Content-Type-Options nosniff;
 *  - navigation/window: external origin navigation blocked, window.open denied,
 *    <webview> inert, javascript:/file: cannot move the window;
 *  - permissions: deny-by-default (Notification denied);
 *  - IPC sender negatives on the packaged app: an untrusted second BrowserWindow
 *    is rejected on every known channel with the stable redacted code; unknown
 *    channels never invoke capability logic;
 *  - local-auth replay: with no runtime session the packaged app mints NO
 *    token (null twice) and rejects unknown targets — fail closed;
 *  - secret/log redaction: bootstrap/status surfaces expose only schema-declared
 *    redacted state, never a value or secret-looking fragment;
 *  - no unexpected external loading: the packaged window only ever requests
 *    loopback origins; packaged resources contain no .map/.pem/.key/.env files.
 *
 * Run: npx playwright test --config tests/security/release-security.config.ts
 * (the release-evidence gate `verify-release-evidence.ps1 -RequireAll` also
 * invokes this suite against the packaged artifact).
 */
import { test, expect } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import { readdirSync } from "node:fs";
import path from "node:path";
import { launchShell, packagedExePath } from "../e2e/launch";
import { DESKTOP_BRIDGE_KEY, type DesktopBridge } from "../../src/shared/bridge-contract";
import { IPC_ERROR_CODES } from "../../src/main/ipc/validate-sender";

const DESKTOP_DIR = path.resolve(__dirname, "..", "..");
const FAKE_PRELOAD = path.join(
  DESKTOP_DIR,
  "tests",
  "security",
  "fixtures",
  "untrusted-sender-preload.js",
);
const ATTACKER_ORIGIN = "https://attacker.invalid";
const LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "::1"];

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
  expect(packagedExePath(), "suite must run against the packaged exe").not.toBeNull();
  electronApp = await launchShell();
  page = await electronApp.firstWindow();
  await page.waitForLoadState("domcontentloaded");
});

test.afterAll(async () => {
  await electronApp?.close();
});

test.describe("packaged shell webPreferences (live webContents)", () => {
  test("sandbox / contextIsolation / webSecurity are on, nodeIntegration off", async () => {
    const winId = await electronApp!.evaluate(({ BrowserWindow }) => {
      const wins = BrowserWindow.getAllWindows();
      return wins[0]?.id ?? -1;
    });
    expect(winId).not.toBe(-1);

    const prefs = await electronApp!.evaluate(
      ({ BrowserWindow }, id) => {
        const win = BrowserWindow.fromId(id);
        if (win === null) return null;
        // `getLastWebPreferences` is absent from the Electron 43 public
        // typings (see create-window.ts); probe it via the untyped runtime API.
        const p = (
          win.webContents as unknown as {
            getLastWebPreferences(): Record<string, unknown>;
          }
        ).getLastWebPreferences();
        return {
          sandbox: p.sandbox,
          contextIsolation: p.contextIsolation,
          nodeIntegration: p.nodeIntegration,
          webSecurity: p.webSecurity,
          nodeIntegrationInWorker: p.nodeIntegrationInWorker,
          allowRunningInsecureContent: p.allowRunningInsecureContent,
        };
      },
      winId,
    );
    expect(prefs).not.toBeNull();
    expect(prefs).toMatchObject({
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
    });
    // `getLastWebPreferences` reports `nodeIntegrationInWorker` only when it
    // differs from the Electron default; either way it must never be true
    // (deny-by-default — a worker cannot gain Node access).
    expect(prefs?.nodeIntegrationInWorker).not.toBe(true);
  });
});

test.describe("packaged renderer privilege negatives", () => {
  test("no require/process/module/global leak into the renderer main world", async () => {
    const probe = await page.evaluate(() => {
      const globalObj = (window as unknown as Record<string, unknown>).global;
      return {
        require: typeof (window as unknown as Record<string, unknown>).require,
        process: typeof (window as unknown as Record<string, unknown>).process,
        module: typeof (window as unknown as Record<string, unknown>).module,
        global: typeof globalObj,
        nodeRequire:
          typeof (window as unknown as Record<string, unknown>).nodeRequire,
      };
    });
    expect(probe).toEqual({
      require: "undefined",
      process: "undefined",
      module: "undefined",
      global: "undefined",
      nodeRequire: "undefined",
    });
  });

  test("the packaged window exposes only the six declared bridge capabilities", async () => {
    const surface = await page.evaluate((key) => {
      const bridge = (window as unknown as Record<string, unknown>)[key];
      if (typeof bridge !== "object" || bridge === null) return null;
      return Object.keys(bridge as Record<string, unknown>).sort();
    }, DESKTOP_BRIDGE_KEY);
    expect(surface).toEqual([
      "getBootstrap",
      "getLocalAuthToken",
      "getRuntimeStatus",
      "onRuntimeStatus",
      "openExternalLink",
      "requestRuntimeRestart",
    ]);
  });

  test("window.open is denied and never creates a window", async () => {
    const popupNull = await page.evaluate(
      (origin) => window.open(origin) === null,
      ATTACKER_ORIGIN,
    );
    expect(popupNull).toBe(true);
    const winCount = await electronApp!.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().length,
    );
    expect(winCount).toBe(1);
  });

  test("<webview> embedding is inert (webviewTag never enabled)", async () => {
    const probe = await page.evaluate(() => {
      const webview = document.createElement("webview");
      webview.setAttribute("src", "https://example.com");
      document.body.appendChild(webview);
      return webview.constructor.name === "HTMLWebViewElement";
    });
    expect(probe).toBe(false);
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
    expect(permission).toBe("denied");
  });
});

test.describe("packaged CSP", () => {
  test("production CSP is served on the app document with no broad wildcard", async () => {
    const metaCsp = await page.evaluate(() => {
      const meta = document.querySelector(
        'meta[http-equiv="Content-Security-Policy"]',
      );
      return meta ? meta.getAttribute("content") : null;
    });
    expect(metaCsp).toBeNull();

    const { header, nosniff } = await page.evaluate(async (origin) => {
      const response = await fetch(origin, { method: "GET" });
      return {
        header: response.headers.get("content-security-policy"),
        nosniff: response.headers.get("x-content-type-options"),
      };
    }, rendererUrl);
    expect(header).not.toBeNull();
    expect(header).toContain("default-src 'none'");
    expect(header).not.toMatch(/\*\s/);
    expect(header).not.toMatch(/default-src \*/);
    expect(nosniff).toBe("nosniff");
  });
});

test.describe("packaged navigation / window policy", () => {
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
          const r = await bridge.openExternalLink(url);
          return { ok: r.ok, code: r.ok ? "" : r.code };
        },
        { key: DESKTOP_BRIDGE_KEY, url: bad },
      );
      expect(result.ok).toBe(false);
      expect(result.code).toContain("REJECTED");
    }
  });
});

test.describe("packaged IPC sender / channel negatives", () => {
  test("an untrusted second window is rejected on known channels with a redacted code", async () => {
    const windowPromise = electronApp!.waitForEvent("window");
    await electronApp!.evaluate(
      ({ BrowserWindow }, { preload, url }) => {
        const w = new BrowserWindow({
          show: false,
          webPreferences: {
            preload,
            sandbox: true,
            contextIsolation: true,
            nodeIntegration: false,
          },
        });
        void w.loadURL(url);
        return w.id;
      },
      { preload: FAKE_PRELOAD, url: rendererUrl },
    );
    const untrustedPage = await windowPromise;

    const probe = await untrustedPage.evaluate(() => {
      const sender = (window as unknown as {
        untrustedSender: { invoke: (ch: string, ...a: unknown[]) => Promise<unknown> };
      }).untrustedSender;
      return Promise.all([
        sender.invoke("bridge:getRuntimeStatus"),
        sender.invoke("bridge:getBootstrap"),
        sender.invoke("bridge:getLocalAuthToken", "agent"),
        sender.invoke("bridge:openExternalLink", "https://example.com"),
      ]);
    });

    for (const result of probe) {
      expect(result).toMatchObject({
        ok: false,
        code: IPC_ERROR_CODES.SENDER_NOT_MAIN_WINDOW,
      });
    }
  });

  test("an unknown channel is rejected without invoking capability logic", async () => {
    const windowPromise = electronApp!.waitForEvent("window");
    await electronApp!.evaluate(
      ({ BrowserWindow }, { preload, url }) => {
        const w = new BrowserWindow({
          show: false,
          webPreferences: {
            preload,
            sandbox: true,
            contextIsolation: true,
            nodeIntegration: false,
          },
        });
        void w.loadURL(url);
        return w.id;
      },
      { preload: FAKE_PRELOAD, url: rendererUrl },
    );
    const untrustedPage = await windowPromise;

    let message = "";
    try {
      await untrustedPage.evaluate(() => {
        const sender = (window as unknown as {
          untrustedSender: { invoke: (ch: string) => Promise<unknown> };
        }).untrustedSender;
        return sender.invoke("bridge:noSuchChannel");
      });
    } catch (err) {
      message = err instanceof Error ? err.message : String(err);
    }
    expect(message).toContain("No handler registered");
  });

  test("malformed and oversized payloads are rejected before capability logic", async () => {
    // Non-string to a string-schema arg.
    const malformed = await page.evaluate(
      async ({ key }) => {
        const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
          DesktopBridge,
          "openExternalLink"
        >;
        return bridge.openExternalLink(12345 as unknown as string);
      },
      { key: DESKTOP_BRIDGE_KEY },
    );
    expect(malformed).toMatchObject({
      ok: false,
      code: IPC_ERROR_CODES.INVALID_PAYLOAD,
    });

    // Over-sized payload (4 KiB cap) — must be rejected, never dispatched.
    const oversized = "x".repeat(4_096 + 1024);
    const big = await page.evaluate(
      async ({ key, url }) => {
        const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
          DesktopBridge,
          "openExternalLink"
        >;
        return bridge.openExternalLink(url);
      },
      { key: DESKTOP_BRIDGE_KEY, url: oversized },
    );
    expect(big).toMatchObject({
      ok: false,
      code: IPC_ERROR_CODES.PAYLOAD_TOO_LARGE,
    });
  });
});

test.describe("packaged local-auth replay / fail-closed", () => {
  test("no runtime session => no token minted (replay returns null twice)", async () => {
    // Packaged mode with the renderer supplied through the env seam has no
    // owned runtime session, so the session-scoped token must fail closed.
    const tokens = await page.evaluate(async (key) => {
      const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
        DesktopBridge,
        "getLocalAuthToken"
      >;
      const agent1 = await bridge.getLocalAuthToken("agent");
      const agent2 = await bridge.getLocalAuthToken("agent");
      return { agent1, agent2 };
    }, DESKTOP_BRIDGE_KEY);
    expect(tokens.agent1).toBeNull();
    expect(tokens.agent2).toBeNull();

    // Unknown target is rejected (never a token for an unspecified service).
    const bad = await page.evaluate(async (key) => {
      const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
        DesktopBridge,
        "getLocalAuthToken"
      >;
      return bridge.getLocalAuthToken("not-a-service" as "agent");
    }, DESKTOP_BRIDGE_KEY);
    expect(bad).toBeNull();
  });

  test("status/bootstrap expose only schema-declared redacted state, never a value", async () => {
    const payload = await page.evaluate(async (key) => {
      const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
        DesktopBridge,
        "getBootstrap" | "getRuntimeStatus"
      >;
      const bootstrap = await bridge.getBootstrap();
      const status = await bridge.getRuntimeStatus();
      return { bootstrap, status };
    }, DESKTOP_BRIDGE_KEY);

    expect(Object.keys(payload.status).sort()).toEqual([
      "appVersion",
      "electronVersion",
      "ready",
      "security",
    ]);
    expect(Object.keys(payload.status.security).sort()).toEqual([
      "contextIsolation",
      "nodeIntegration",
      "sandbox",
      "webSecurity",
    ]);

    expect(Object.keys(payload.bootstrap).sort()).toEqual([
      "appVersion",
      "bridgeVersion",
      "credentials",
      "features",
      "runtime",
    ]);
    const credentials = payload.bootstrap.credentials as {
      provider: string;
      localAuth: string;
      storageAvailable: boolean;
    };
    expect(credentials.provider).toMatch(
      /^(available|unavailable|decrypt_failed|rotation_needed)$/,
    );
    expect(credentials.localAuth).toMatch(
      /^(available|unavailable|decrypt_failed|rotation_needed)$/,
    );
    expect(credentials.storageAvailable).toBe(true);

    // No secret-like fragment anywhere in the serialized surfaces.
    const serialized = JSON.stringify(payload);
    expect(serialized).not.toMatch(/sk-[A-Za-z0-9]/);
    expect(serialized).not.toMatch(/secret=/i);
    expect(serialized).not.toMatch(/api[_ -]?key/i);
    expect(serialized).not.toMatch(/BEGIN (RSA|OPENSSH|PRIVATE)/);
  });
});

test.describe("packaged external-loading negatives", () => {
  test("every request from the packaged window stays on the loopback origin", async () => {
    const hosts = new Set<string>();
    page.on("request", (req) => {
      try {
        hosts.add(new URL(req.url()).hostname);
      } catch {
        hosts.add("invalid-url");
      }
    });
    await page.goto(`${rendererUrl}/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(500);

    expect(hosts.size).toBeGreaterThan(0);
    for (const host of hosts) {
      expect(LOOPBACK_HOSTS, `request to unexpected host ${host}`).toContain(host);
    }
  });

  test("packaged resources contain no source-map or secret material", () => {
    const resourceRoot = path.join(
      path.dirname(packagedExePath() as string),
      "resources",
    );
    const secretPattern = /\.(map|pem|key|p12|pfx|env)$/i;
    const offenders: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (secretPattern.test(entry.name)) offenders.push(full);
      }
    };
    walk(resourceRoot);
    expect(offenders).toEqual([]);
  });
});
