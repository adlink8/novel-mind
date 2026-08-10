/**
 * Optional desktop bridge typing (Phase 42, Plan 42-03, Task 1).
 *
 * `window.novelMindDesktop` exists ONLY inside the NovelMind Electron shell
 * (exposed by `desktop/src/preload/index.ts` via `contextBridge` under the
 * key `novelMindDesktop`). In a plain browser tab it is absent and business
 * code must degrade via `@/lib/desktop/capabilities` — never by importing
 * Electron or probing Node globals (D-42-06 / T-42-03-01).
 *
 * The type is a type-only import of the shared bridge contract, so the
 * renderer's view of the bridge is exactly the contract main/preload
 * implement — a single source of truth across the trust boundary. The import
 * is erased at compile time; no Electron code ever reaches the web bundle.
 */
import type { DesktopBridge } from "../../../desktop/src/shared/bridge-contract";

declare global {
  interface Window {
    /** Present only when running inside the Electron shell; undefined in browser mode. */
    novelMindDesktop?: DesktopBridge;
  }
}

export {};
