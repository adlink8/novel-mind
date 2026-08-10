/**
 * Main-owned local session authentication (Phase 44, plan 44-02, Task 2).
 *
 * The Electron main process mints SEPARATE short-lived, audience-bound session
 * tokens for the backend and the Agent Service. Validation contract enforced on
 * the service side (backend `desktop_local_auth.py`, agent-service
 * `desktop-local-auth.ts`):
 *
 *   iss = "novelmind-desktop-main"
 *   aud = backend "novelmind-desktop-local" | agent "novelmind-agent-local"
 *   exp / iat / jti / sid(session-bound)
 *
 * Security properties:
 * - The HMAC secret is per-instance random and rotates on every runtime
 *   restart (`rotate()`), so a token minted for a prior session is rejected
 *   after the secret rotates (T-44-02-03).
 * - Tokens are short-lived (`LOCAL_AUTH_TOKEN_TTL_MS`), bound to the runtime
 *   session id (`sid`), and never written to logs or to the renderer bundle
 *   (T-44-02-01).
 * - `tokens()` returns null while no active session exists — the caller can
 *   never mint for a session-less runtime.
 *
 * Pure Node (crypto only) — no Electron imports — so the module is directly
 * unit-testable and reusable by the process adapters / bootstrap wiring.
 */
import { createHmac, randomBytes } from "node:crypto";

export const LOCAL_AUTH_ISSUER = "novelmind-desktop-main";
export const LOCAL_AUTH_BACKEND_AUDIENCE = "novelmind-desktop-local";
export const LOCAL_AUTH_AGENT_AUDIENCE = "novelmind-agent-local";
export const LOCAL_AUTH_TOKEN_TTL_MS = 5 * 60 * 1000;

/** The two local services that consume separately-audienced session tokens. */
export const LOCAL_AUTH_TARGETS = ["backend", "agent"] as const;
export type LocalAuthTarget = (typeof LOCAL_AUTH_TARGETS)[number];

export interface LocalAuthTokenSet {
  /** Audience `novelmind-desktop-local` — for the FastAPI backend. */
  backend: string;
  /** Audience `novelmind-agent-local` — for the Agent Service. */
  agent: string;
}

export interface DesktopLocalAuthOptions {
  /** Current runtime session id; null means "no active session". */
  sessionId: () => string | null;
  /** Injected clock (deterministic expiry tests). */
  now?: () => Date;
  /** Injected secret factory (deterministic tests; default per-instance random). */
  secret?: () => string;
  /** Token lifetime (default 5 minutes). */
  ttlMs?: number;
}

function b64url(value: Buffer): string {
  return value.toString("base64url");
}

function hmacSign(input: string, secret: string): string {
  return createHmac("sha256", secret).update(input, "utf8").digest("base64url");
}

function mintJwt(claims: Record<string, string | number>, secret: string): string {
  const header = b64url(Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const payload = b64url(Buffer.from(JSON.stringify(claims)));
  const signingInput = `${header}.${payload}`;
  return `${signingInput}.${hmacSign(signingInput, secret)}`;
}

/**
 * Desktop session credential minting. Main process only. `rotate()` changes the
 * HMAC secret so every previously minted token is invalidated immediately
 * (used on runtime restart and on explicit re-bootstrap).
 */
export class DesktopLocalAuth {
  private readonly sessionId: () => string | null;
  private readonly now: () => Date;
  private readonly secretFactory: () => string;
  private readonly ttlMs: number;
  private currentSecret: string;

  constructor(options: DesktopLocalAuthOptions) {
    this.sessionId = options.sessionId;
    this.now = options.now ?? (() => new Date());
    this.secretFactory = options.secret ?? (() => randomBytes(32).toString("hex"));
    this.ttlMs = options.ttlMs ?? LOCAL_AUTH_TOKEN_TTL_MS;
    this.currentSecret = this.secretFactory();
  }

  /** The current HMAC secret. Injected into owned service environments so they
   * can verify the audience/expiry-bound tokens (never logged, never rendered). */
  secret(): string {
    return this.currentSecret;
  }

  /** Rotate the secret: every previously minted token fails signature checks. */
  rotate(): void {
    this.currentSecret = this.secretFactory();
  }

  /**
   * Mint a fresh token for each local service, bound to the current session.
   * Returns null when no runtime session is active (callers fail closed).
   */
  tokens(): LocalAuthTokenSet | null {
    const sid = this.sessionId();
    if (sid === null) return null;
    const nowMs = this.now().getTime();
    const claims = {
      iss: LOCAL_AUTH_ISSUER,
      iat: Math.floor(nowMs / 1000),
      exp: Math.floor((nowMs + this.ttlMs) / 1000),
      jti: randomBytes(12).toString("hex"),
      sid,
    };
    return {
      backend: mintJwt({ ...claims, aud: LOCAL_AUTH_BACKEND_AUDIENCE }, this.currentSecret),
      agent: mintJwt({ ...claims, aud: LOCAL_AUTH_AGENT_AUDIENCE }, this.currentSecret),
    };
  }

  /** Redacted health signal for the status surface — never a token value. */
  isConfigured(): boolean {
    return this.currentSecret.length >= 32;
  }
}
