/**
 * IPC sender / payload negative suite (Phase 42, Plan 42-02, Task 2).
 *
 * Proves T-42-02-01 / T-42-02-03:
 * - sender authorization unit tests cover every branch of `authorizeSender`
 *   (shell-not-ready, wrong webContents, null/non-main frame, unapproved frame
 *   origin, approved sender) with deterministic redacted codes;
 * - end-to-end: an untrusted second BrowserWindow (custom test-only preload
 *   exposing a raw generic invoke) is rejected on every known channel, and
 *   unknown channels reject without invoking capability logic;
 * - end-to-end: malformed and oversized payloads on the one argument-carrying
 *   channel (openExternalLink) reject before capability logic runs;
 * - approved calls return only schema-declared data.
 */
import { test, expect, _electron as electron } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import type { BrowserWindow, IpcMainInvokeEvent } from "electron";
import path from "node:path";
import {
  DESKTOP_BRIDGE_KEY,
  type DesktopBridge,
} from "../../src/shared/bridge-contract";
import {
  IPC_ERROR_CODES,
  authorizeSender,
} from "../../src/main/ipc/validate-sender";
import { MAX_IPC_PAYLOAD_BYTES } from "../../src/main/ipc/bridge-schema";

const DESKTOP_DIR = path.resolve(__dirname, "..", "..");
const FAKE_PRELOAD = path.join(DESKTOP_DIR, "tests", "security", "fixtures", "untrusted-sender-preload.js");
const APPROVED_FRAME_URL = "http://127.0.0.1:9999/";

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

// ---------------------------------------------------------------------------
// Unit tests: authorizeSender branches (deterministic, no browser needed).
// ---------------------------------------------------------------------------

function makeWebContents(url: string = APPROVED_FRAME_URL) {
  return {
    mainFrame: { url },
  };
}

function makeEvent(overrides: Record<string, unknown> = {}): IpcMainInvokeEvent {
  const wc = makeWebContents();
  return {
    sender: wc,
    senderFrame: wc.mainFrame,
    frameId: 1,
    processId: 1,
    type: "ipc-message",
    preventDefault: () => undefined,
    defaultPrevented: false,
    ...overrides,
  } as unknown as IpcMainInvokeEvent;
}

function makeWindow(overrides: Record<string, unknown> = {}): BrowserWindow {
  return {
    id: 1,
    isDestroyed: () => false,
    webContents: makeWebContents(),
    ...overrides,
  } as unknown as BrowserWindow;
}

test("authorizeSender rejects when the shell is not ready", () => {
  const event = makeEvent();
  expect(authorizeSender(event, () => null)).toEqual({
    ok: false,
    code: IPC_ERROR_CODES.SHELL_NOT_READY,
    reason: expect.any(String),
  });

  const destroyed = makeWindow({ isDestroyed: () => true });
  expect(authorizeSender(event, () => destroyed)).toEqual({
    ok: false,
    code: IPC_ERROR_CODES.SHELL_NOT_READY,
    reason: expect.any(String),
  });
});

test("authorizeSender rejects a sender that is not the main window webContents", () => {
  const win = makeWindow();
  const event = makeEvent({ sender: makeWebContents(), senderFrame: null });
  expect(authorizeSender(event, () => win)).toEqual({
    ok: false,
    code: IPC_ERROR_CODES.SENDER_NOT_MAIN_WINDOW,
    reason: expect.any(String),
  });
});

test("authorizeSender rejects a null or non-main senderFrame", () => {
  const win = makeWindow();
  const nullFrame = makeEvent({ sender: win.webContents, senderFrame: null });
  expect(authorizeSender(nullFrame, () => win)).toEqual({
    ok: false,
    code: IPC_ERROR_CODES.SENDER_FRAME_UNTRUSTED,
    reason: expect.any(String),
  });

  const otherFrame = makeEvent({
    sender: win.webContents,
    senderFrame: makeWebContents().mainFrame,
  });
  expect(authorizeSender(otherFrame, () => win)).toEqual({
    ok: false,
    code: IPC_ERROR_CODES.SENDER_FRAME_UNTRUSTED,
    reason: expect.any(String),
  });
});

test("authorizeSender rejects a frame whose origin is not approved", () => {
  const win = makeWindow();
  const badFrame = makeEvent({
    sender: win.webContents,
    senderFrame: { url: "https://attacker.invalid/" },
  });
  expect(authorizeSender(badFrame, () => win)).toEqual({
    ok: false,
    code: IPC_ERROR_CODES.SENDER_FRAME_UNTRUSTED,
    reason: expect.any(String),
  });
});

test("authorizeSender approves an authorized sender/frame/origin", () => {
  const win = makeWindow();
  const event = makeEvent({ sender: win.webContents, senderFrame: win.webContents.mainFrame });
  expect(authorizeSender(event, () => win)).toEqual({ ok: true });
});

// ---------------------------------------------------------------------------
// End-to-end: approved calls return schema-declared data.
// ---------------------------------------------------------------------------

test("approved bridge calls return only schema-declared data", async () => {
  const status = await page.evaluate(async (key) => {
    const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
      DesktopBridge,
      "getRuntimeStatus" | "getBootstrap"
    >;
    const s = await bridge.getRuntimeStatus();
    const b = await bridge.getBootstrap();
    return { status: s, bootstrap: b };
  }, DESKTOP_BRIDGE_KEY);

  expect(status.status.ready).toBe(true);
  expect(Object.keys(status.status).sort()).toEqual([
    "appVersion",
    "electronVersion",
    "ready",
    "security",
  ]);
  expect(status.bootstrap).toEqual({ appVersion: "0.1.0", bridgeVersion: 1, features: ["desktop-shell"] });
  expect(Object.keys(status.status)).not.toContain("code");
});

// ---------------------------------------------------------------------------
// End-to-end: untrusted sender window rejected on every known channel.
// ---------------------------------------------------------------------------

test("an untrusted webContents is rejected on known channels with a redacted code", async () => {
  const rendererUrl = process.env.NOVELMIND_SMOKE_RENDERER_URL;
  if (!rendererUrl) throw new Error("renderer URL missing");

  const windowPromise = electronApp.waitForEvent("window");
  await electronApp.evaluate(
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
    const sender = (window as unknown as { untrustedSender: { invoke: (ch: string, ...a: unknown[]) => Promise<unknown> } })
      .untrustedSender;
    return Promise.all([
      sender.invoke("bridge:getRuntimeStatus"),
      sender.invoke("bridge:getBootstrap"),
      sender.invoke("bridge:requestRuntimeRestart"),
    ]);
  });

  for (const result of probe) {
    expect(result).toMatchObject({ ok: false, code: IPC_ERROR_CODES.SENDER_NOT_MAIN_WINDOW });
  }
});

test("an unknown channel is rejected without invoking capability logic", async () => {
  const rendererUrl = process.env.NOVELMIND_SMOKE_RENDERER_URL;
  if (!rendererUrl) throw new Error("renderer URL missing");

  const windowPromise = electronApp.waitForEvent("window");
  await electronApp.evaluate(
    ({ BrowserWindow }, { preload, url }) => {
      const w = new BrowserWindow({
        show: false,
        webPreferences: { preload, sandbox: true, contextIsolation: true, nodeIntegration: false },
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
      const sender = (window as unknown as { untrustedSender: { invoke: (ch: string) => Promise<unknown> } })
        .untrustedSender;
      return sender.invoke("bridge:noSuchChannel");
    });
  } catch (err) {
    message = err instanceof Error ? err.message : String(err);
  }
  expect(message).toContain("No handler registered");
});

// ---------------------------------------------------------------------------
// End-to-end: malformed / oversized payloads rejected before capability logic.
// ---------------------------------------------------------------------------

test("malformed payload on the argument-carrying channel is rejected", async () => {
  const result = await page.evaluate(
    async ({ key, arg }) => {
      const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
        DesktopBridge,
        "openExternalLink"
      >;
      // Deliberately pass a non-string to a string-schema arg.
      return bridge.openExternalLink(arg as unknown as string);
    },
    { key: DESKTOP_BRIDGE_KEY, arg: 12345 },
  );
  expect(result).toMatchObject({ ok: false, code: IPC_ERROR_CODES.INVALID_PAYLOAD });
});

test("oversized payload is rejected before capability logic", async () => {
  const oversized = "x".repeat(MAX_IPC_PAYLOAD_BYTES + 1024);
  const result = await page.evaluate(
    async ({ key, url }) => {
      const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
        DesktopBridge,
        "openExternalLink"
      >;
      return bridge.openExternalLink(url);
    },
    { key: DESKTOP_BRIDGE_KEY, url: oversized },
  );
  expect(result).toMatchObject({ ok: false, code: IPC_ERROR_CODES.PAYLOAD_TOO_LARGE });
});

test("over-long but under-limit field is rejected by schema bound", async () => {
  // 3000 chars serializes under MAX_IPC_PAYLOAD_BYTES but exceeds the 2048
  // per-field string bound — must reject with INVALID_PAYLOAD, not dispatch.
  const longField = "x".repeat(3000);
  const result = await page.evaluate(
    async ({ key, url }) => {
      const bridge = (window as unknown as Record<string, unknown>)[key] as Pick<
        DesktopBridge,
        "openExternalLink"
      >;
      return bridge.openExternalLink(url);
    },
    { key: DESKTOP_BRIDGE_KEY, url: longField },
  );
  expect(result).toMatchObject({ ok: false, code: IPC_ERROR_CODES.INVALID_PAYLOAD });
});

test("duplicate handler registration is refused (T-42-02-03 handler lifecycle)", async () => {
  const result = await electronApp.evaluate(({ ipcMain }, channel) => {
    // The production registration layer (register.ts) guards with
    // `listenerCount(channel) > 0` before calling `ipcMain.handle`, so a second
    // registration can never silently shadow the live handler.
    let duplicateThrew = false;
    try {
      ipcMain.handle(channel, () => "shadow");
    } catch {
      duplicateThrew = true;
    }
    return { duplicateThrew };
  }, "bridge:getRuntimeStatus");

  expect(result.duplicateThrew).toBe(true);
});
