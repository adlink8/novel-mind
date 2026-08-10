/**
 * Desktop capability resolver unit tests (Phase 42, Plan 42-03).
 *
 * Browser mode (no `window.novelMindDesktop`) must report deterministic
 * unsupported states without throwing; Electron mode must forward to the
 * bridge and preserve typed results. The fixture bridge below mirrors the
 * contract surface from `desktop/src/shared/bridge-contract.ts`.
 */
import { afterEach, describe, expect, it } from "vitest";
import { desktopCapabilities } from "./capabilities";
import type {
  DesktopBridge,
  DesktopRuntimeStatus,
} from "../../../../desktop/src/shared/bridge-contract";

const STATUS: DesktopRuntimeStatus = {
  ready: true,
  appVersion: "0.1.0",
  electronVersion: "43.3.0",
  security: { sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true },
};

function makeBridge(overrides: Partial<DesktopBridge> = {}): DesktopBridge {
  return {
    getRuntimeStatus: async () => STATUS,
    requestRuntimeRestart: async () => ({ ok: true }),
    getBootstrap: async () => ({
      appVersion: "0.1.0",
      bridgeVersion: 1,
      features: ["desktop-shell"],
      runtime: null,
    }),
    openExternalLink: async (url) =>
      url.startsWith("https://") ? { ok: true } : { ok: false, code: "REJECTED", reason: "not https" },
    onRuntimeStatus: (listener) => {
      const id = window.setInterval(() => listener(STATUS), 1000);
      return {
        unsubscribe: () => window.clearInterval(id),
      };
    },
    ...overrides,
  };
}

function withBridge(bridge: DesktopBridge) {
  (window as unknown as Record<string, unknown>)["novelMindDesktop"] = bridge;
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>)["novelMindDesktop"];
});

describe("browser mode (no bridge)", () => {
  it("isDesktop is false", () => {
    expect(desktopCapabilities.isDesktop).toBe(false);
  });

  it("getRuntimeStatus returns unsupported without throwing", async () => {
    await expect(desktopCapabilities.getRuntimeStatus()).resolves.toEqual({
      supported: false,
      reason: "bridge-unavailable",
    });
  });

  it("getBootstrap / requestRuntimeRestart / openExternalLink degrade the same way", async () => {
    await expect(desktopCapabilities.getBootstrap()).resolves.toEqual({
      supported: false,
      reason: "bridge-unavailable",
    });
    await expect(desktopCapabilities.requestRuntimeRestart()).resolves.toEqual({
      supported: false,
      reason: "bridge-unavailable",
    });
    await expect(desktopCapabilities.openExternalLink("https://example.com")).resolves.toEqual({
      supported: false,
      reason: "bridge-unavailable",
    });
  });

  it("onRuntimeStatus returns null (no subscription leaks)", () => {
    expect(desktopCapabilities.onRuntimeStatus(() => {})).toBeNull();
  });
});

describe("electron mode (bridge present)", () => {
  it("isDesktop is true and the bridge is surfaced", () => {
    withBridge(makeBridge());
    expect(desktopCapabilities.isDesktop).toBe(true);
    expect(desktopCapabilities.bridge).not.toBeNull();
  });

  it("forwards capability calls and preserves typed values", async () => {
    withBridge(makeBridge());
    await expect(desktopCapabilities.getRuntimeStatus()).resolves.toEqual({
      supported: true,
      value: STATUS,
    });
    await expect(desktopCapabilities.getBootstrap()).resolves.toEqual({
      supported: true,
      value: {
        appVersion: "0.1.0",
        bridgeVersion: 1,
        features: ["desktop-shell"],
        runtime: null,
      },
    });
    await expect(desktopCapabilities.openExternalLink("https://example.com")).resolves.toEqual({
      supported: true,
      value: { ok: true },
    });
    await expect(desktopCapabilities.openExternalLink("javascript:alert(1)")).resolves.toEqual({
      supported: true,
      value: { ok: false, code: "REJECTED", reason: "not https" },
    });
  });

  it("onRuntimeStatus subscribes and returns an unsubscribe handle", () => {
    withBridge(makeBridge());
    const sub = desktopCapabilities.onRuntimeStatus(() => {});
    expect(sub).not.toBeNull();
    expect(typeof sub?.unsubscribe).toBe("function");
    sub?.unsubscribe();
  });

  it("a malformed bridge object is treated as absent (fail closed)", async () => {
    (window as unknown as Record<string, unknown>)["novelMindDesktop"] = {
      getRuntimeStatus: "not-a-function",
    };
    expect(desktopCapabilities.isDesktop).toBe(false);
    await expect(desktopCapabilities.getRuntimeStatus()).resolves.toEqual({
      supported: false,
      reason: "bridge-unavailable",
    });
  });
});
