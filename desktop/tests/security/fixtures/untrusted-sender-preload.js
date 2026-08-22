/**
 * TEST-ONLY preload for the IPC negative suite (Phase 42, Plan 42-02).
 *
 * Loaded by a throwaway BrowserWindow created inside the ipc.spec.ts test to
 * prove the MAIN process rejects senders it does not own. It exposes a raw,
 * generic `invoke` so the test can fire at known channels, malformed payloads,
 * oversized payloads and unknown channels from an untrusted webContents.
 *
 * This file is NEVER loaded by a production window and is never compiled into
 * dist/ (tsconfig.build only compiles src/). Production preload stays
 * capability-only (src/preload/index.ts).
 */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("untrustedSender", {
  invoke: (channel, ...args) => ipcRenderer.invoke(channel, ...args),
});
