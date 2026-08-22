/**
 * IPC sender authorization (D-42-05 / T-42-02-01).
 *
 * Every handler must run `assertTrustedSender` BEFORE touching payload
 * parsing or capability logic. A message is authorized only when:
 * - the shell is ready (main window exists and is alive);
 * - the sender webContents IS the main window's webContents (not a popup, not
 *   another window);
 * - the sender frame is the main frame of that window;
 * - the sender frame origin is the approved loopback app origin.
 *
 * On failure the handler returns a stable, redacted error code. Error codes
 * are fixed strings with no interpolation of URLs, origins, frames or payload
 * content, so logs and renderer-visible errors leak nothing about the
 * environment (T-42-02-01 spoofing / plan must-have).
 */
import { BrowserWindow } from "electron";
import type { IpcMainInvokeEvent } from "electron";
import { isApprovedAppUrl } from "../security/approved-origin";

/** Stable IPC rejection codes (redacted — no runtime values interpolated). */
export const IPC_ERROR_CODES = {
  SHELL_NOT_READY: "DESKTOP_ERR::IPC::SHELL_NOT_READY",
  SENDER_NOT_MAIN_WINDOW: "DESKTOP_ERR::IPC::SENDER_NOT_MAIN_WINDOW",
  SENDER_FRAME_UNTRUSTED: "DESKTOP_ERR::IPC::SENDER_FRAME_UNTRUSTED",
  UNKNOWN_CHANNEL: "DESKTOP_ERR::IPC::UNKNOWN_CHANNEL",
  DUPLICATE_REGISTRATION: "DESKTOP_ERR::IPC::DUPLICATE_REGISTRATION",
  PAYLOAD_TOO_LARGE: "DESKTOP_ERR::IPC::PAYLOAD_TOO_LARGE",
  INVALID_PAYLOAD: "DESKTOP_ERR::IPC::INVALID_PAYLOAD",
} as const;

export interface IpcRejection {
  ok: false;
  code: string;
  reason: string;
}

/** All rejection payloads share this stable, redacted shape. */
export function rejection(code: string, reason: string): IpcRejection {
  return { ok: false, code, reason };
}

/** Main window provider indirection so the module is unit-testable. */
export type MainWindowProvider = () => BrowserWindow | null;

/**
 * Authorizes an IPC event. Returns the event if trusted, or a redacted
 * rejection if the sender, frame or origin is not approved. Never throws.
 */
export function authorizeSender(
  event: IpcMainInvokeEvent,
  getMainWindow: MainWindowProvider,
): { ok: true } | IpcRejection {
  const win = getMainWindow();
  if (win === null || win.isDestroyed()) {
    return rejection(IPC_ERROR_CODES.SHELL_NOT_READY, "shell is not ready");
  }
  if (event.sender !== win.webContents) {
    return rejection(IPC_ERROR_CODES.SENDER_NOT_MAIN_WINDOW, "sender is not the main window");
  }
  const frame = event.senderFrame;
  if (frame === null || frame !== win.webContents.mainFrame) {
    return rejection(IPC_ERROR_CODES.SENDER_FRAME_UNTRUSTED, "sender frame is not the main frame");
  }
  const frameUrl = frame.url;
  if (frameUrl === undefined || !isApprovedAppUrl(frameUrl)) {
    return rejection(IPC_ERROR_CODES.SENDER_FRAME_UNTRUSTED, "sender frame origin is not approved");
  }
  return { ok: true };
}
