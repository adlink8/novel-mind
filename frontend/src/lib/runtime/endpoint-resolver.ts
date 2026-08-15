/**
 * Single logical-service endpoint seam (Phase 44, plan 44-01, Task 2).
 *
 * Route/API code must never learn desktop-specific port logic. This module is
 * the ONLY place that maps the desktop runtime's one-session bootstrap into the
 * base URLs the HTTP client and the SSE stream use:
 *
 * - Electron mode: resolves `apiBaseUrl`/`agentBaseUrl` from the typed
 *   `BootstrapSession` produced by the main process (dynamically allocated
 *   loopback endpoints — never fixed packaged ports, D-44-01). The session is
 *   cached by `sessionId`; a runtime restart rotates the session and the
 *   resolver deterministically rebuilds.
 * - Browser mode: falls back to the existing relative rewrite routes (`/api`,
 *   `/agent`), which `next.config.mjs` proxies during development.
 * - Missing/stale/malformed bootstrap: typed `unavailable` state — never a
 *   guessed URL, never a frozen `NEXT_PUBLIC_*` value (T-44-01-01/02).
 *
 * This module is browser-safe (no Node/Electron imports) and depends only on
 * the desktop capability resolver at runtime.
 */
import type {
  BootstrapEndpoint,
  BootstrapSession,
} from "../../../../desktop/src/shared/bootstrap-contract";
import { desktopCapabilities } from "../desktop/capabilities";

/** The only host the renderer may dial from bootstrap data (T-44-01-01). */
const LOOPBACK_HOST = "127.0.0.1";

export type EndpointUnavailableReason =
  | "not-ready"
  | "expired"
  | "invalidated"
  | "malformed";

/**
 * Base URLs for the two logical services.
 * - `apiBaseUrl`: axios baseURL equivalent — browser `/api`, desktop
 *   `http://127.0.0.1:<port>/api` (absolute mirror of the rewrite route).
 * - `agentBaseUrl`: origin to prepend to relative `/agent/...` SSE paths —
 *   browser `""` (paths stay relative), desktop `http://127.0.0.1:<port>`.
 */
export interface ResolvedEndpoints {
  apiBaseUrl: string;
  agentBaseUrl: string;
}

export type EndpointResolution =
  | { kind: "desktop"; sessionId: string; endpoints: ResolvedEndpoints }
  | { kind: "browser"; endpoints: ResolvedEndpoints }
  | { kind: "unavailable"; reason: EndpointUnavailableReason };

/** Absolute origin of a validated loopback endpoint, or null if not loopback. */
function originOf(endpoint: BootstrapEndpoint): string | null {
  if (endpoint.host !== LOOPBACK_HOST) return null;
  if (!Number.isInteger(endpoint.port) || endpoint.port <= 0 || endpoint.port > 65535) {
    return null;
  }
  return `http://${endpoint.host}:${endpoint.port}`;
}

function endpointsFromSession(session: BootstrapSession): ResolvedEndpoints | null {
  const apiOrigin = originOf(session.services.api);
  const agentOrigin = originOf(session.services.agent);
  if (apiOrigin === null || agentOrigin === null) return null;
  // `services.api` is the FastAPI loopback endpoint; the backend serves its
  // HTTP surface under `/api`, mirroring the browser rewrite route exactly.
  return { apiBaseUrl: `${apiOrigin}/api`, agentBaseUrl: agentOrigin };
}

export class RuntimeEndpointResolver {
  private cached: { sessionId: string; endpoints: ResolvedEndpoints } | null = null;

  /**
   * Resolves the current logical service endpoints. Never throws: every
   * failure mode maps to a typed state.
   */
  async resolve(): Promise<EndpointResolution> {
    if (!desktopCapabilities.isDesktop) {
      this.cached = null;
      return browserResolution();
    }

    const capability = await desktopCapabilities.getBootstrap();
    if (!capability.supported) {
      // Bridge vanished mid-session: degrade to browser routes rather than guess.
      this.cached = null;
      return browserResolution();
    }

    const runtime = capability.value.runtime;
    if (runtime === null) {
      this.cached = null;
      return { kind: "unavailable", reason: "not-ready" };
    }
    if (runtime.status === "unavailable") {
      this.cached = null;
      return { kind: "unavailable", reason: runtime.reason };
    }

    const session = runtime.session;
    if (this.cached !== null && this.cached.sessionId === session.sessionId) {
      // Same session: reuse without another bridge round-trip.
      return { kind: "desktop", sessionId: session.sessionId, endpoints: this.cached.endpoints };
    }

    const endpoints = endpointsFromSession(session);
    if (endpoints === null) {
      this.cached = null;
      return { kind: "unavailable", reason: "malformed" };
    }
    this.cached = { sessionId: session.sessionId, endpoints };
    return { kind: "desktop", sessionId: session.sessionId, endpoints };
  }

  /** Drops the cached desktop session (test hook; restart rotation is automatic). */
  invalidate(): void {
    this.cached = null;
  }
}

function browserResolution(): EndpointResolution {
  return { kind: "browser", endpoints: { apiBaseUrl: "/api", agentBaseUrl: "" } };
}

/** Process-wide resolver shared by the HTTP client and the SSE stream. */
export const endpointResolver = new RuntimeEndpointResolver();
