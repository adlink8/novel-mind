/**
 * Agent Service desktop local-session auth (Phase 44, plan 44-02, Task 2).
 *
 * Requires an audience- and expiry-bound local session token on every inbound
 * agent request. The Electron main mints separate tokens per service; the
 * Agent Service only accepts `aud === "novelmind-agent-local"` with
 * `iss === "novelmind-desktop-main"` and a live `exp`/`iat`/`jti`/`sid`
 * (T-44-02-02). The HMAC secret is injected by the owning main process
 * (env `NOVELMIND_LOCAL_AUTH_SECRET`).
 *
 * Fail-closed guarantees:
 * - No secret configured / missing header / invalid signature / wrong audience
 *   / expired → 401. There is no dev-bypass path here: the desktop adapter
 *   injects a real secret whenever the agent service is launched, and browser
 *   developers run agent-service without this config in their own dev harness
 *   (which keeps the existing gateway-token semantics for the FastAPI side).
 * - The token is only accepted from loopback sources; a LAN request is 401
 *   even with a valid token.
 * - Token values are never logged (V6 / T-44-02-01).
 */

import { createHmac } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";

export const LOCAL_AUTH_ISSUER = "novelmind-desktop-main";
export const LOCAL_AUTH_AGENT_AUDIENCE = "novelmind-agent-local";
export const LOCAL_AUTH_LEEWAY_SECONDS = 60;

/**
 * Loopback sources accepted for the session token. Includes the IPv4-mapped
 * IPv6 form (`::ffff:127.0.0.1`): a dual-stack server bound to `::` presents
 * IPv4 loopback connections that way, and rejecting it would break legitimate
 * local requests (44-03 server wiring).
 */
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"]);

/** Decoded verification result (only stable fields; never the raw token). */
export interface VerifiedLocalSession {
  sid: string;
  jti: string;
  aud: string;
}

function b64urlJson(segment: string): Record<string, unknown> | null {
  try {
    const raw = Buffer.from(segment, "base64url").toString("utf8");
    const parsed = JSON.parse(raw) as unknown;
    return typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/**
 * Verify an HS256 JWT against the injected secret. Constant-time comparison of
 * the signature; every structural/claim failure resolves false (fail closed).
 */
export function verifyLocalSessionToken(
  token: string,
  secret: string,
  nowMs: number = Date.now(),
): VerifiedLocalSession | null {
  if (typeof secret !== "string" || secret.length < 32) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  const header = b64urlJson(parts[0] ?? "");
  if (header?.alg !== "HS256" || header?.typ !== "JWT") return null;

  const claims = b64urlJson(parts[1] ?? "");
  if (claims === null) return null;
  if (claims.iss !== LOCAL_AUTH_ISSUER) return null;
  if (claims.aud !== LOCAL_AUTH_AGENT_AUDIENCE) return null;
  if (typeof claims.exp !== "number" || typeof claims.iat !== "number") return null;
  if (typeof claims.sid !== "string" || typeof claims.jti !== "string") return null;

  const now = nowMs / 1000;
  if (claims.exp + LOCAL_AUTH_LEEWAY_SECONDS < now) return null;
  if (claims.iat > now + LOCAL_AUTH_LEEWAY_SECONDS) return null;

  // Recompute the signature and compare constant-time.
  const signingInput = `${parts[0]}.${parts[1]}`;
  const expected = createHmac("sha256", secret)
    .update(signingInput, "utf8")
    .digest("base64url");
  const received = parts[2] ?? "";
  if (expected.length !== received.length) return null;
  let diff = 0;
  for (let i = 0; i < expected.length; i += 1) {
    diff |= expected.charCodeAt(i) ^ received.charCodeAt(i);
  }
  if (diff !== 0) return null;

  return { sid: claims.sid, jti: claims.jti, aud: claims.aud };
}

/** 401 JSON envelope (frozen shape; no token fragment ever). */
function reject(res: ServerResponse): void {
  res.writeHead(401, { "content-type": "application/json" });
  res.end(JSON.stringify({ error: { code: "unauthorized", message: "缺少有效的本地会话认证" } }));
}

/**
 * Guard for inbound HTTP requests. Returns the verified session when the
 * request is an authenticated loopback request, otherwise writes a 401 and
 * returns null (caller MUST stop processing).
 */
export function requireLocalSession(
  req: IncomingMessage,
  res: ServerResponse,
  secret: string | undefined,
  nowMs: number = Date.now(),
): VerifiedLocalSession | null {
  const source = req.socket.remoteAddress ?? "";
  if (!LOOPBACK_HOSTS.has(source)) {
    reject(res);
    return null;
  }
  if (typeof secret !== "string" || secret === "") {
    // No local-auth material: fail closed, never accept anonymous requests.
    reject(res);
    return null;
  }
  const header = req.headers.authorization;
  if (typeof header !== "string" || !header.startsWith("Bearer ")) {
    reject(res);
    return null;
  }
  const token = header.slice("Bearer ".length).trim();
  if (token === "") {
    reject(res);
    return null;
  }
  const verified = verifyLocalSessionToken(token, secret, nowMs);
  if (verified === null) {
    reject(res);
    return null;
  }
  return verified;
}

/** Extract the end-user Bearer token (forwarded to FastAPI for owner checks). */
export function extractEndUserToken(req: IncomingMessage): string | null {
  const header = req.headers.authorization;
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return null;
  const token = header.slice("Bearer ".length).trim();
  return token === "" ? null : token;
}

/**
 * Build the local-session Authorization header value the renderer/transport
 * attaches to agent requests. When local-auth is configured, the end-user JWT
 * in the transport header is NOT the session credential the agent service
 * verifies — main mints a separate short-lived `novelmind-agent-local` token
 * which the renderer obtains through the bridge and prepends, so the final
 * header is `Bearer <session-token> <end-user-jwt>`. When no session material
 * exists (browser mode / no secret) the end-user JWT is passed through
 * unchanged so existing browser dev semantics are preserved.
 */
export function buildLocalAuthHeader(
  endUserToken: string | null,
  localAuthToken: string | null,
): string {
  const endUser = endUserToken?.trim() ?? "";
  if (localAuthToken !== null && localAuthToken !== "") {
    return `Bearer ${localAuthToken} ${endUser}`;
  }
  return endUser ? `Bearer ${endUser}` : "";
}

/** Redacted label for diagnostics — never a token value. */
export function describeLocalAuth(secretConfigured: boolean): string {
  return secretConfigured ? "configured" : "unconfigured";
}
