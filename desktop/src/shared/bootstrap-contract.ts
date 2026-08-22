/**
 * One-session runtime bootstrap contract (Phase 44, plan 44-01).
 *
 * PURE module — no Node/Electron imports — so it crosses the main→renderer
 * trust boundary exactly like `bridge-contract.ts`. It carries ONLY dynamically
 * allocated loopback endpoints and bounded session metadata: never provider
 * keys, environment, filesystem paths or process details (D-44-01 / D-44-02 /
 * T-44-01-02).
 *
 * The renderer endpoint resolver maps the session's logical services into the
 * api/agent bases; route code never learns desktop port logic (44-CONTEXT:
 * "Centralize URL selection so route code does not learn desktop-specific port
 * logic"). This is runtime session data — never a fixed build-time public env
 * value (D-44-01).
 */

import type { RuntimeComponent } from "../runtime/types";

/**
 * Bounded session lifetime. After expiry the provider deterministically issues
 * a fresh session (new session id), so a long-lived renderer never dials a
 * stale session.
 */
export const BOOTSTRAP_SESSION_TTL_MS = 60 * 60 * 1000;

/** The only host the renderer may ever dial from bootstrap data (T-44-01-01). */
export const BOOTSTRAP_LOOPBACK_HOST = "127.0.0.1" as const;

/** One OS-allocated loopback endpoint. Never a fixed port. */
export interface BootstrapEndpoint {
  host: typeof BOOTSTRAP_LOOPBACK_HOST;
  /** OS-allocated port; always > 0 after allocation. */
  port: number;
}

/** Logical renderer-facing service handles derived from the component graph. */
export const BOOTSTRAP_SERVICES = ["api", "agent", "renderer"] as const;
export type BootstrapService = (typeof BOOTSTRAP_SERVICES)[number];

/** Bounded capability flags the renderer may rely on for this session. */
export interface BootstrapCapabilities {
  /** Agent SSE streaming is available while the agent service is ready. */
  agentStreaming: boolean;
}

/** One session of the managed local runtime, as seen by the renderer. */
export interface BootstrapSession {
  /** Unique per runtime session; rotates on restart and on expiry. */
  sessionId: string;
  /** ISO timestamp the session was issued (runtime entered ready). */
  issuedAt: string;
  /** ISO timestamp after which the session must be re-resolved. */
  expiresAt: string;
  /** Dynamically allocated loopback endpoints of the five managed components. */
  components: Readonly<Record<RuntimeComponent, BootstrapEndpoint>>;
  /** Logical api/agent/renderer handles derived from the component set. */
  services: Readonly<Record<BootstrapService, BootstrapEndpoint>>;
  capabilities: BootstrapCapabilities;
}

/**
 * Deterministic bootstrap the bridge may deliver for a window session.
 * `ready` only ever carries an allowlisted, loopback-bound session;
 * every other outcome is a typed unavailable state with a stable reason.
 */
export type RuntimeBootstrap =
  | { status: "ready"; session: BootstrapSession }
  | {
      status: "unavailable";
      reason: "not-ready" | "expired" | "invalidated" | "malformed";
    };
