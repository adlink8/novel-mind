/**
 * Content-Security-Policy for the production shell (D-42-07 / T-42-02-02).
 *
 * The policy is injected on the main-frame document response via
 * `session.webRequest.onHeadersReceived` — not a `<meta>` tag — so the
 * renderer cannot weaken or remove it. Browsers ignore unknown CSP tokens, so
 * removing the header, stripping it, or adding a permissive `<meta>` can never
 * relax this policy.
 *
 * Development mode is an explicit opt-in used only by the local Electron
 * launcher. It adds `unsafe-eval` because React development diagnostics require
 * it. Packaged applications and direct test launches remain on the production
 * policy even if an inherited environment variable is present.
 */
import type {
  HeadersReceivedResponse,
  OnHeadersReceivedListenerDetails,
  Session,
} from "electron";
import { isApprovedAppUrl } from "./approved-origin";

/**
 * Every source directive is deny-by-default (`default-src 'none'`). The Next
 * renderer legitimately ships inline scripts (theme boot script + RSC
 * `__next_f` payloads) and inline styles, so `script-src` and `style-src`
 * carry `'unsafe-inline'` — scoped to the local app origin only (the page
 * itself is served by us over the approved loopback origin; there is no
 * remote script/style source). All other resource classes are denied unless
 * explicitly allowed.
 */
export const CSP_DIRECTIVES = [
  "default-src 'none'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-src 'none'",
  "frame-ancestors 'none'",
  "worker-src 'self'",
].join("; ");

export type CspMode = "development" | "production";

const DEVELOPMENT_CSP_DIRECTIVES = CSP_DIRECTIVES.replace(
  "script-src 'self' 'unsafe-inline'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
);

export function cspDirectivesForMode(mode: CspMode): string {
  return mode === "development" ? DEVELOPMENT_CSP_DIRECTIVES : CSP_DIRECTIVES;
}

/**
 * Applies the production CSP by rewriting the `Content-Security-Policy`
 * response header of every HTTP(S) response served from an approved loopback
 * origin. Only loopback-origin responses are touched — other traffic is passed
 * through untouched. Registration is on the given session; a fresh session gets
 * a fresh registration, so tests and future windows are isolated.
 */
export function applyCspToSession(ses: Session, mode: CspMode = "production"): void {
  const directives = cspDirectivesForMode(mode);
  ses.webRequest.onHeadersReceived(
    (
      details: OnHeadersReceivedListenerDetails,
      callback: (response: HeadersReceivedResponse) => void,
    ): void => {
      if (!isApprovedAppUrl(details.url)) {
        callback({});
        return;
      }
      const responseHeaders = { ...details.responseHeaders };
      responseHeaders["Content-Security-Policy"] = [directives];
      responseHeaders["X-Content-Type-Options"] = ["nosniff"];
      callback({ responseHeaders });
    },
  );
}
