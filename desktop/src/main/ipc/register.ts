/**
 * Single-point IPC registration (D-42-05 / T-42-02-03).
 *
 * All main-process IPC handlers flow through `registerBridgeIpcHandlers`, which
 * enforces, in order, for every message:
 *   1. SENDER: webContents == main window, frame == main frame, frame origin
 *      approved (T-42-02-01);
 *   2. CHANNEL: known capability channel — unknown channels reject with a
 *      stable code (T-42-02-03);
 *   3. SIZE: serialized payload <= MAX_IPC_PAYLOAD_BYTES (T-42-02-03);
 *   4. SHAPE: each arg validated against its bounded schema before any
 *      capability logic runs;
 *   5. REGISTRATION: duplicate registrations are refused — a second call on
 *      the same ipcMain cannot silently shadow the first.
 *
 * Lifecycle: `registerBridgeIpcHandlers` records every registration so
 * `unregisterBridgeIpcHandlers` can remove them on shutdown/reload
 * (T-42-02-03 handler lifecycle). All rejection payloads are the stable,
 * redacted `{ ok:false, code, reason }` shape — never a raw Error and never an
 * interpolated URL/origin/payload fragment.
 */
import { ipcMain } from "electron";
import type { IpcMainInvokeEvent } from "electron";
import type { BrowserWindow } from "electron";
import {
  MAX_IPC_PAYLOAD_BYTES,
  BRIDGE_IPC_SCHEMAS,
  validateArg,
} from "./bridge-schema";
import {
  IPC_ERROR_CODES,
  authorizeSender,
  rejection,
} from "./validate-sender";

export type MainWindowProvider = () => BrowserWindow | null;
export type BridgeHandler = (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown;

export interface BridgeIpcRegistration {
  channel: string;
  handler: BridgeHandler;
  /** Recorded so `unregisterBridgeIpcHandlers` can remove them on shutdown. */
  mainWindowProvider: MainWindowProvider;
}

const registeredHandlers = new Set<BridgeIpcRegistration>();

/**
 * Registers the full bridge IPC surface. Throws on duplicate registration so a
 * buggy reload path cannot silently shadow live handlers.
 */
export function registerBridgeIpcHandlers(
  mainWindowProvider: MainWindowProvider,
  capabilityHandlers: Record<string, BridgeHandler>,
): void {
  for (const channel of Object.keys(BRIDGE_IPC_SCHEMAS)) {
    const schema = BRIDGE_IPC_SCHEMAS[channel];
    if (schema === undefined) continue;
    const capabilityHandler = capabilityHandlers[channel];
    if (capabilityHandler === undefined) {
      throw new Error(`no capability handler registered for channel "${channel}"`);
    }

    const handler: BridgeHandler = (event, ...args) => {
      // 1. Sender authorization before any parsing or capability logic.
      const senderCheck = authorizeSender(event, mainWindowProvider);
      if (!senderCheck.ok) return senderCheck;

      // 2. Bounded payload size (DoS bound).
      let serializedSize = 0;
      try {
        serializedSize = Buffer.byteLength(JSON.stringify(args), "utf8");
      } catch {
        return rejection(IPC_ERROR_CODES.INVALID_PAYLOAD, "payload is not serializable");
      }
      if (serializedSize > MAX_IPC_PAYLOAD_BYTES) {
        return rejection(IPC_ERROR_CODES.PAYLOAD_TOO_LARGE, "payload exceeds size limit");
      }

      // 3. Schema validation before capability logic.
      for (let i = 0; i < schema.args.length; i += 1) {
        const argSchema = schema.args[i];
        if (argSchema === undefined) continue;
        const argCheck = validateArg(argSchema, args[i]);
        if (!argCheck.ok) return rejection(IPC_ERROR_CODES.INVALID_PAYLOAD, argCheck.reason);
      }

      // 4. Approved call — dispatch to the capability.
      return capabilityHandler(event, ...args);
    };

    if (ipcMain.listenerCount(channel) > 0) {
      throw new Error(`duplicate IPC registration for channel "${channel}"`);
    }
    ipcMain.handle(channel, handler);
    registeredHandlers.add({ channel, handler, mainWindowProvider });
  }
}

/**
 * Removes every handler registered by this module (shutdown/reload lifecycle).
 * Idempotent.
 */
export function unregisterBridgeIpcHandlers(): void {
  for (const registration of registeredHandlers) {
    ipcMain.removeHandler(registration.channel);
  }
  registeredHandlers.clear();
}

/** For tests: whether a registration set is currently live. */
export function isBridgeIpcRegistered(channel: string): boolean {
  for (const registration of registeredHandlers) {
    if (registration.channel === channel) return true;
  }
  return false;
}

export { IPC_ERROR_CODES };
