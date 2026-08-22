/**
 * Permission policy for the production shell (D-42-03).
 *
 * Deny-by-default: no geolocation, camera/mic/media, notifications, clipboard,
 * HID, serial, USB, pointer lock, fullscreen, MIDI or any other permission
 * request is ever granted. The renderer is a local UI over a loopback origin;
 * it needs none of these, and a compromised renderer gains nothing by asking.
 *
 * `setPermissionCheckHandler` (also used by the clipboard/paste path) is set
 * to a blanket denial so the check path cannot diverge from the request path.
 * `openExternal` permission requests are denied — the shell's only external
 * path is the validated `openExternalLink` capability (see navigation.ts),
 * never a renderer-initiated permission grant.
 */
import type { Session } from "electron";

/** Applies the deny-all permission policy to a session. */
export function applyPermissionPolicy(ses: Session): void {
  ses.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  ses.setPermissionCheckHandler(() => false);
}
