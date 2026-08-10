/**
 * Navigation, window and download policy for the production shell
 * (D-42-03 / T-42-02-02).
 *
 * Deny-by-default:
 * - hard navigations / redirects / subframe navigations are allowed only
 *   within the approved loopback app origin;
 * - `window.open` (popups / new windows) never creates a shell window;
 * - `<webview>` embedding is prevented;
 * - downloads are prevented.
 *
 * External links (D-42-03): the shell never hands renderer-controlled
 * arguments to `shell.openExternal`. The only external-link path is the
 * explicit, main-side capability `openExternalLink`, which accepts strictly
 * validated HTTPS URLs (no credentials, no other scheme) and opens them in the
 * OS default browser. The renderer contributes no argv/flags/env — only a URL,
 * and only after main-side validation.
 */
import { shell } from "electron";
import type { BrowserWindow } from "electron";
import { isApprovedAppUrl } from "./approved-origin";

/** Stable, redacted rejection codes for the external-link capability. */
export const EXTERNAL_LINK_ERROR_CODES = {
  REJECTED: "DESKTOP_ERR::EXTERNAL_LINK::REJECTED",
  OPEN_FAILED: "DESKTOP_ERR::EXTERNAL_LINK::OPEN_FAILED",
} as const;

export type OpenExternalLinkResult =
  | { ok: true }
  | { ok: false; code: string; reason: string };

/**
 * Main-side validation for external links: only `https:` with a real hostname
 * and no embedded credentials. Anything else (javascript:, file:, data:,
 * custom schemes, http:) is rejected.
 */
export function isSafeExternalUrl(raw: string): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== "https:") return false;
  if (url.hostname === "") return false;
  if (url.username !== "" || url.password !== "") return false;
  return true;
}

/** Opener seam so the capability is unit-testable without a real browser. */
export interface ExternalLinkOpener {
  openExternal(url: string): Promise<void>;
}

const defaultOpener: ExternalLinkOpener = {
  openExternal: (url) => shell.openExternal(url),
};

/**
 * The explicit external-link capability. Opens a link in the OS default
 * browser only after `isSafeExternalUrl` passes. The returned error carries a
 * stable redacted code — never a renderer-controlled string, URL or path.
 */
export async function openExternalLink(
  raw: string,
  opener: ExternalLinkOpener = defaultOpener,
): Promise<OpenExternalLinkResult> {
  if (!isSafeExternalUrl(raw)) {
    return { ok: false, code: EXTERNAL_LINK_ERROR_CODES.REJECTED, reason: "not a validated https url" };
  }
  try {
    await opener.openExternal(raw);
  } catch {
    return { ok: false, code: EXTERNAL_LINK_ERROR_CODES.OPEN_FAILED, reason: "external browser failed to open" };
  }
  return { ok: true };
}

/**
 * Applies the deny-by-default navigation/window/download policy to a window.
 * Safe to call once per window at creation time.
 */
export function applyNavigationPolicy(win: BrowserWindow): void {
  const wc = win.webContents;

  // Popups / new windows are denied. There is no legitimate `window.open`
  // surface in the current app; if a first-party external-link feature ships,
  // it must route through `openExternalLink` — never through renderer-supplied
  // shell arguments.
  wc.setWindowOpenHandler((details) => {
    if (isSafeExternalUrl(details.url)) {
      void openExternalLink(details.url);
    }
    return { action: "deny" };
  });

  // <webview> embedding is denied.
  wc.on("will-attach-webview", (event) => event.preventDefault());

  // Hard navigation in the main frame is only allowed within the approved
  // loopback app origin.
  wc.on("will-navigate", (event) => {
    if (!isApprovedAppUrl(event.url)) event.preventDefault();
  });

  // Subframe navigations are denied to non-approved origins (defense-in-depth;
  // the app ships no iframes).
  wc.on("will-frame-navigate", (event) => {
    if (!isApprovedAppUrl(event.url)) event.preventDefault();
  });

  // Server redirects are only allowed within the approved loopback app origin.
  wc.on("will-redirect", (event) => {
    if (!isApprovedAppUrl(event.url)) event.preventDefault();
  });

  // Downloads are denied.
  wc.session.on("will-download", (event) => event.preventDefault());
}
