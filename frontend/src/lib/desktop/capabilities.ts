/**
 * Optional desktop capability resolver (Phase 42, Plan 42-03, Task 1).
 *
 * The Electron shell is an optional runtime: browser mode has no
 * `window.novelMindDesktop` bridge. Business/route code must never import
 * Electron or infer desktop mode from Node globals (D-42-06 / T-42-03-01);
 * it reads capability state only through this module. Every capability
 * returns a deterministic supported/unsupported state so the UI can degrade
 * gracefully (no exceptions) when the bridge is absent.
 *
 * The bridge types come from the shared contract via type-only imports
 * (erased at compile time); only the resolver itself is part of the web
 * bundle, and it depends on nothing from `desktop/` at runtime.
 */
import type {
  DesktopBootstrap,
  DesktopBridge,
  DesktopRuntimeStatus,
  OpenExternalLinkResult,
  RestartRequestResult,
  RuntimeStatusListener,
  RuntimeStatusSubscription,
} from "../../../../desktop/src/shared/bridge-contract";

/** One typed capability result: value when supported, stable reason when not. */
export type DesktopCapability<T> =
  | { supported: true; value: T }
  | { supported: false; reason: "bridge-unavailable" };

function unsupported<T>(): DesktopCapability<T> {
  return { supported: false, reason: "bridge-unavailable" };
}

/**
 * The bridge key mirrors `DESKTOP_BRIDGE_KEY` in the shared contract. It is
 * deliberately a plain string here (not a value import from `desktop/`) so the
 * web bundle keeps zero runtime coupling to the desktop package. Drift between
 * this string and the contract is caught end-to-end by the shell smoke suite
 * and by the desktop `renderer-privileges` suite (asserting the exact key).
 */
const DESKTOP_BRIDGE_KEY = "novelMindDesktop";

function resolveBridge(): DesktopBridge | null {
  if (typeof window === "undefined") return null;
  const maybeBridge = (window as unknown as Record<string, unknown>)[DESKTOP_BRIDGE_KEY];
  return isDesktopBridge(maybeBridge) ? maybeBridge : null;
}

function isDesktopBridge(value: unknown): value is DesktopBridge {
  if (typeof value !== "object" || value === null) return false;
  const bridge = value as Record<string, unknown>;
  return (
    typeof bridge.getRuntimeStatus === "function" &&
    typeof bridge.requestRuntimeRestart === "function" &&
    typeof bridge.getBootstrap === "function" &&
    typeof bridge.openExternalLink === "function" &&
    typeof bridge.onRuntimeStatus === "function"
  );
}

/**
 * Typed, optional desktop capability surface. Safe to call from any route in
 * both browser and Electron modes; absent bridge yields `supported: false`
 * instead of throwing.
 */
export const desktopCapabilities = {
  /** True only when the Electron bridge is actually present in this window. */
  get isDesktop(): boolean {
    return resolveBridge() !== null;
  },

  /** The live bridge when running in the Electron shell, otherwise null. */
  get bridge(): DesktopBridge | null {
    return resolveBridge();
  },

  async getRuntimeStatus(): Promise<DesktopCapability<DesktopRuntimeStatus>> {
    const bridge = resolveBridge();
    if (bridge === null) return unsupported();
    return { supported: true, value: await bridge.getRuntimeStatus() };
  },

  async getBootstrap(): Promise<DesktopCapability<DesktopBootstrap>> {
    const bridge = resolveBridge();
    if (bridge === null) return unsupported();
    return { supported: true, value: await bridge.getBootstrap() };
  },

  async openExternalLink(
    url: string,
  ): Promise<DesktopCapability<OpenExternalLinkResult>> {
    const bridge = resolveBridge();
    if (bridge === null) return unsupported();
    return { supported: true, value: await bridge.openExternalLink(url) };
  },

  async requestRuntimeRestart(): Promise<DesktopCapability<RestartRequestResult>> {
    const bridge = resolveBridge();
    if (bridge === null) return unsupported();
    return { supported: true, value: await bridge.requestRuntimeRestart() };
  },

  /** Subscribe to runtime status changes; null when no bridge is present. */
  onRuntimeStatus(
    listener: RuntimeStatusListener,
  ): RuntimeStatusSubscription | null {
    const bridge = resolveBridge();
    if (bridge === null) return null;
    return bridge.onRuntimeStatus(listener);
  },
};
