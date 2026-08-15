/**
 * The single origin predicate for the production shell.
 *
 * Every trust decision — window creation, navigation, redirects, CSP header
 * injection, external-link capability and IPC sender validation — derives from
 * this one allowlist: loopback HTTP (`127.0.0.1`, `localhost`, `[::1]`) on any
 * port. Ports stay dynamic until Phase 43 pins the packaged origin
 * (D-41-05 dynamic-port contract).
 *
 * Pure module (no Electron/Node imports) so the main process and the shared
 * contract can both rely on it.
 */
export function isApprovedAppUrl(raw: string): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== "http:") return false;
  const host = url.hostname;
  return host === "127.0.0.1" || host === "localhost" || host === "::1";
}
