/**
 * Preload script — the only bridge between the sandboxed renderer and the
 * Electron main process.
 *
 * Sandbox contract:
 * - The BrowserWindow runs `sandbox: true` + `contextIsolation: true` +
 *   `nodeIntegration: false` (see src/main/create-window.ts).
 * - Exposes exactly the four DesktopBridge capabilities. No `ipcRenderer`, no
 *   filesystem, shell, env or process object ever crosses the boundary.
 * - This file must stay SELF-CONTAINED: a sandboxed preload cannot `require`
 *   local modules at runtime, so runtime values are inlined below and the
 *   shared contract is imported type-only (erased at compile time).
 */
import { contextBridge, ipcRenderer } from "electron";
import type { IpcRendererEvent } from "electron";
import type {
  DesktopBridge,
  DesktopBootstrap,
  DesktopRuntimeStatus,
  RestartRequestResult,
  RuntimeStatusListener,
} from "../shared/bridge-contract";

/**
 * MUST MATCH `DESKTOP_IPC_CHANNELS` in `src/shared/bridge-contract.ts` (the
 * main process imports the shared constants; the sandboxed preload inlines
 * them). Drift is caught by the shell smoke suite end-to-end.
 */
const CHANNELS = {
  getRuntimeStatus: "bridge:getRuntimeStatus",
  requestRuntimeRestart: "bridge:requestRuntimeRestart",
  getBootstrap: "bridge:getBootstrap",
  runtimeStatusChanged: "bridge:runtimeStatusChanged",
} as const;

/** MUST MATCH `DESKTOP_BRIDGE_KEY` in the shared contract. */
const BRIDGE_KEY = "novelMindDesktop";

const bridge: DesktopBridge = {
  getRuntimeStatus: (): Promise<DesktopRuntimeStatus> =>
    ipcRenderer.invoke(CHANNELS.getRuntimeStatus),

  requestRuntimeRestart: (): Promise<RestartRequestResult> =>
    ipcRenderer.invoke(CHANNELS.requestRuntimeRestart),

  getBootstrap: (): Promise<DesktopBootstrap> =>
    ipcRenderer.invoke(CHANNELS.getBootstrap),

  onRuntimeStatus(listener: RuntimeStatusListener) {
    const handler = (_event: IpcRendererEvent, status: DesktopRuntimeStatus): void => {
      listener(status);
    };
    ipcRenderer.on(CHANNELS.runtimeStatusChanged, handler);
    return {
      unsubscribe: (): void => {
        ipcRenderer.removeListener(CHANNELS.runtimeStatusChanged, handler);
      },
    };
  },
};

contextBridge.exposeInMainWorld(BRIDGE_KEY, bridge);
