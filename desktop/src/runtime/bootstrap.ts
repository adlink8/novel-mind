/**
 * Main-owned one-session bootstrap producer (Phase 44, plan 44-01, Task 1).
 *
 * Derives the renderer-visible `RuntimeBootstrap` from the managed runtime
 * snapshot. Rules:
 * - A session CANNOT be produced before the runtime is fully ready (D-43-09);
 *   a null runtime or any non-ready snapshot yields `unavailable("not-ready")`.
 * - Only the five managed components are ever surfaced, and only after
 *   loopback validation (T-44-01-01): a ready component with a missing or
 *   non-loopback endpoint fails closed as `unavailable("malformed")`.
 * - The session is bound to one runtime start (`startedAt`). A restart rotates
 *   the session id deterministically; an endpoint change within a session
 *   (targeted restart) also rebuilds it, so a stale cached session is never
 *   served (44-01 acceptance: "expires/invalidates deterministically").
 * - Sessions expire after BOOTSTRAP_SESSION_TTL_MS and are regenerated with a
 *   fresh session id.
 * - Never serializes secrets, provider keys, environment, filesystem paths or
 *   process details (T-44-01-02).
 *
 * This module is pure Node (no Electron imports) so the runtime unit suite can
 * exercise it directly; `main/index.ts` wires it to the bridge capability.
 */
import { randomUUID } from "node:crypto";
import {
  BOOTSTRAP_LOOPBACK_HOST,
  BOOTSTRAP_SESSION_TTL_MS,
  type BootstrapCapabilities,
  type BootstrapEndpoint,
  type BootstrapService,
  type BootstrapSession,
  type RuntimeBootstrap,
} from "../shared/bootstrap-contract";
import type { DesktopRuntime } from "./desktop-runtime";
import type { RuntimeComponent, RuntimeSnapshot } from "./types";

/** Logical service → component mapping (only the three renderer-facing handles). */
const SERVICE_COMPONENT: Readonly<Record<BootstrapService, RuntimeComponent>> = {
  api: "fastapi",
  agent: "agent_service",
  renderer: "next",
};

export interface RuntimeBootstrapProviderOptions {
  /** The owned runtime, or null before the runtime is created. */
  runtime: () => DesktopRuntime | null;
  /** Injected session-id factory (deterministic tests). */
  sessionId?: () => string;
  /** Injected clock (deterministic expiry tests). */
  now?: () => Date;
}

interface CachedSession {
  /** The runtime start the cached session is bound to. */
  startedAt: string;
  session: BootstrapSession;
}

/** Loopback host/port validation — defense even though adapters guarantee it. */
function isLoopbackEndpoint(value: unknown): value is BootstrapEndpoint {
  if (typeof value !== "object" || value === null) return false;
  const endpoint = value as Record<string, unknown>;
  return (
    endpoint.host === BOOTSTRAP_LOOPBACK_HOST &&
    typeof endpoint.port === "number" &&
    Number.isInteger(endpoint.port) &&
    endpoint.port > 0 &&
    endpoint.port <= 65535
  );
}

export class RuntimeBootstrapProvider {
  private readonly runtime: () => DesktopRuntime | null;
  private readonly sessionId: () => string;
  private readonly now: () => Date;
  private cached: CachedSession | null = null;

  constructor(options: RuntimeBootstrapProviderOptions) {
    this.runtime = options.runtime;
    this.sessionId = options.sessionId ?? randomUUID;
    this.now = options.now ?? (() => new Date());
  }

  /**
   * Current bootstrap. Never throws: every failure is a typed unavailable
   * state. Reuses the cached session only while it is bound to the current
   * runtime start, unexpired and still matching the live endpoint data.
   */
  async get(): Promise<RuntimeBootstrap> {
    const runtime = this.runtime();
    if (runtime === null) {
      this.cached = null;
      return { status: "unavailable", reason: "not-ready" };
    }

    const snapshot = await runtime.status();
    if (!snapshot.ready) {
      // Shutdown / degraded / failed: no session may be served (fail closed).
      this.cached = null;
      return { status: "unavailable", reason: "not-ready" };
    }

    const cached = this.cached;
    if (cached !== null && this.isCurrent(cached, snapshot)) {
      return { status: "ready", session: cached.session };
    }

    const session = this.buildSession(snapshot);
    if (session === null) {
      this.cached = null;
      return { status: "unavailable", reason: "malformed" };
    }
    this.cached = { startedAt: snapshot.startedAt ?? "", session };
    return { status: "ready", session };
  }

  /** Drops any cached session (runtime restart/shutdown). Next get() rebuilds. */
  invalidate(): void {
    this.cached = null;
  }

  private isCurrent(cached: CachedSession, snapshot: RuntimeSnapshot): boolean {
    if (cached.startedAt !== (snapshot.startedAt ?? "")) return false;
    if (this.now().getTime() >= Date.parse(cached.session.expiresAt)) return false;
    // Defensive: never reuse a session whose live endpoints drifted.
    return snapshot.components.every((component) => {
      const expected = cached.session.components[component.id];
      return (
        component.endpoint !== null &&
        expected !== undefined &&
        expected.host === component.endpoint.host &&
        expected.port === component.endpoint.port
      );
    });
  }

  /** Builds a fresh session; returns null when any endpoint is malformed. */
  private buildSession(snapshot: RuntimeSnapshot): BootstrapSession | null {
    const components: Partial<Record<RuntimeComponent, BootstrapEndpoint>> = {};
    for (const component of snapshot.components) {
      if (component.state !== "ready") return null;
      if (!isLoopbackEndpoint(component.endpoint)) return null;
      components[component.id] = component.endpoint;
    }

    // Every one of the five components must be present (the caller only builds
    // from a fully ready snapshot, so all keys are populated here).
    const allComponents: Record<RuntimeComponent, BootstrapEndpoint> = {
      next: components.next!,
      fastapi: components.fastapi!,
      agent_service: components.agent_service!,
      postgres_pgvector: components.postgres_pgvector!,
      vector_store: components.vector_store!,
    };

    const services: Partial<Record<BootstrapService, BootstrapEndpoint>> = {};
    for (const service of Object.keys(SERVICE_COMPONENT) as BootstrapService[]) {
      const endpoint = allComponents[SERVICE_COMPONENT[service]];
      if (endpoint === undefined) return null;
      services[service] = endpoint;
    }

    const now = this.now();
    const capabilities: BootstrapCapabilities = {
      agentStreaming: snapshot.components.some(
        (component) => component.id === "agent_service" && component.ready,
      ),
    };
    return {
      sessionId: this.sessionId(),
      issuedAt: now.toISOString(),
      expiresAt: new Date(now.getTime() + BOOTSTRAP_SESSION_TTL_MS).toISOString(),
      components: allComponents,
      services: services as Record<BootstrapService, BootstrapEndpoint>,
      capabilities,
    };
  }
}
