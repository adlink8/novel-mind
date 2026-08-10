/**
 * RuntimeEndpointResolver unit tests (Phase 44, plan 44-01, Task 2/3).
 *
 * Covers:
 * - browser mode (no bridge) → relative rewrite routes (`/api`, `""`),
 * - desktop ready session → dynamic loopback api/agent bases from the typed
 *   bootstrap (never fixed packaged ports),
 * - missing bootstrap (runtime null) → typed unavailable("not-ready"),
 * - expired/invalidated bootstrap → typed unavailable,
 * - malformed endpoint (non-loopback host) → typed unavailable("malformed"),
 * - session rotation (runtime restart) invalidates the stale cached endpoint,
 * - same-session resolves reuse the cache without a second bridge round-trip,
 * - bridge vanishing mid-session degrades to browser routes.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { RuntimeEndpointResolver } from "../endpoint-resolver";
import type {
  DesktopBridge,
  DesktopRuntimeStatus,
  RuntimeStatusListener,
} from "../../../../../desktop/src/shared/bridge-contract";
import type {
  BootstrapEndpoint,
  BootstrapSession,
  RuntimeBootstrap,
} from "../../../../../desktop/src/shared/bootstrap-contract";

const STATUS: DesktopRuntimeStatus = {
  ready: true,
  appVersion: "0.1.0",
  electronVersion: "43.3.0",
  security: { sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true },
};

/** A ready session for the fixture — dynamic ports, loopback only. */
function makeSession(overrides: Partial<BootstrapSession> = {}): BootstrapSession {
  const base = {
    next: { host: "127.0.0.1" as const, port: 41003 },
    fastapi: { host: "127.0.0.1" as const, port: 41001 },
    agent_service: { host: "127.0.0.1" as const, port: 41002 },
    postgres_pgvector: { host: "127.0.0.1" as const, port: 41000 },
    vector_store: { host: "127.0.0.1" as const, port: 41004 },
  };
  return {
    sessionId: "sess-abc",
    issuedAt: "2026-08-10T00:00:00.000Z",
    expiresAt: "2026-08-10T01:00:00.000Z",
    components: base,
    services: { api: base.fastapi, agent: base.agent_service, renderer: base.next },
    capabilities: { agentStreaming: true },
    ...overrides,
  };
}

function readyBootstrap(session: BootstrapSession): RuntimeBootstrap {
  return { status: "ready", session };
}

function makeBridge(bootstrap: RuntimeBootstrap): DesktopBridge & { calls: () => number } {
  let calls = 0;
  const bridge: DesktopBridge = {
    getRuntimeStatus: async () => STATUS,
    requestRuntimeRestart: async () => ({ ok: true }),
    getBootstrap: async () => {
      calls += 1;
      return {
        appVersion: "0.1.0",
        bridgeVersion: 1,
        features: ["desktop-shell"],
        runtime: bootstrap,
        credentials: {
          provider: "unavailable",
          localAuth: "unavailable",
          storageAvailable: false,
        },
      };
    },
    openExternalLink: async (url: string) =>
      url.startsWith("https://") ? { ok: true } : { ok: false, code: "REJECTED", reason: "not https" },
    onRuntimeStatus: (listener: RuntimeStatusListener) => {
      const id = window.setInterval(() => listener(STATUS), 1000);
      return { unsubscribe: () => window.clearInterval(id) };
    },
    getLocalAuthToken: async () => null,
  };
  return Object.assign(bridge, { calls: () => calls });
}

function withBridge(bridge: DesktopBridge) {
  (window as unknown as Record<string, unknown>)["novelMindDesktop"] = bridge;
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>)["novelMindDesktop"];
  vi.restoreAllMocks();
});

describe("browser mode (no bridge)", () => {
  it("falls back to the existing relative rewrite routes", async () => {
    const resolver = new RuntimeEndpointResolver();
    await expect(resolver.resolve()).resolves.toEqual({
      kind: "browser",
      endpoints: { apiBaseUrl: "/api", agentBaseUrl: "" },
    });
  });
});

describe("desktop mode (bridge present)", () => {
  it("resolves dynamic loopback api/agent bases from a ready session", async () => {
    const bridge = makeBridge(readyBootstrap(makeSession()));
    withBridge(bridge);
    const resolver = new RuntimeEndpointResolver();

    await expect(resolver.resolve()).resolves.toEqual({
      kind: "desktop",
      sessionId: "sess-abc",
      endpoints: {
        apiBaseUrl: "http://127.0.0.1:41001/api",
        agentBaseUrl: "http://127.0.0.1:41002",
      },
    });
  });

  it("reuses the cached endpoint mapping across resolves (bridge still consulted for rotation)", async () => {
    const bridge = makeBridge(readyBootstrap(makeSession()));
    withBridge(bridge);
    const resolver = new RuntimeEndpointResolver();

    await resolver.resolve();
    expect(bridge.calls()).toBe(1);
    // The bridge is consulted again so a restarted runtime (new session id) is
    // detected; the endpoint computation is cached and reused.
    await expect(resolver.resolve()).resolves.toEqual({
      kind: "desktop",
      sessionId: "sess-abc",
      endpoints: {
        apiBaseUrl: "http://127.0.0.1:41001/api",
        agentBaseUrl: "http://127.0.0.1:41002",
      },
    });
    expect(bridge.calls()).toBe(2);
  });

  it("reports typed unavailable(not-ready) when the runtime is not ready", async () => {
    const bridge = makeBridge({ status: "unavailable", reason: "not-ready" });
    withBridge(bridge);
    const resolver = new RuntimeEndpointResolver();

    await expect(resolver.resolve()).resolves.toEqual({
      kind: "unavailable",
      reason: "not-ready",
    });
  });

  it("propagates expired/invalidated bootstrap reasons", async () => {
    const bridge = makeBridge({ status: "unavailable", reason: "expired" });
    withBridge(bridge);
    const resolver = new RuntimeEndpointResolver();
    await expect(resolver.resolve()).resolves.toEqual({
      kind: "unavailable",
      reason: "expired",
    });
  });

  it("fails closed with unavailable(malformed) on a non-loopback endpoint", async () => {
    const session = makeSession();
    // The type enforces loopback; cast deliberately violates it for the test.
    const evil = { host: "10.0.0.5", port: 9999 } as unknown as BootstrapEndpoint;
    session.services = {
      api: evil,
      agent: session.services.agent,
      renderer: session.services.renderer,
    };
    const bridge = makeBridge(readyBootstrap(session));
    withBridge(bridge);
    const resolver = new RuntimeEndpointResolver();

    await expect(resolver.resolve()).resolves.toEqual({
      kind: "unavailable",
      reason: "malformed",
    });
  });

  it("rotates to the new session after a runtime restart (no stale cache)", async () => {
    let session = makeSession();
    const bridge = makeBridge(readyBootstrap(session));
    withBridge(bridge);
    const resolver = new RuntimeEndpointResolver();

    await resolver.resolve();
    expect(bridge.calls()).toBe(1);

    // Runtime restart → new session with new id and new ports (both the
    // component set and the logical service handles rotate together).
    const rotatedComponents = {
      next: { host: "127.0.0.1" as const, port: 42003 },
      fastapi: { host: "127.0.0.1" as const, port: 42001 },
      agent_service: { host: "127.0.0.1" as const, port: 42002 },
      postgres_pgvector: { host: "127.0.0.1" as const, port: 42000 },
      vector_store: { host: "127.0.0.1" as const, port: 42004 },
    };
    session = makeSession({
      sessionId: "sess-rotated",
      components: rotatedComponents,
      services: {
        api: rotatedComponents.fastapi,
        agent: rotatedComponents.agent_service,
        renderer: rotatedComponents.next,
      },
    });
    // Re-point the bridge at the new bootstrap.
    const newBridge = makeBridge(readyBootstrap(session));
    withBridge(newBridge);

    await expect(resolver.resolve()).resolves.toEqual({
      kind: "desktop",
      sessionId: "sess-rotated",
      endpoints: {
        apiBaseUrl: "http://127.0.0.1:42001/api",
        agentBaseUrl: "http://127.0.0.1:42002",
      },
    });
  });

  it("degrades to browser routes when the bridge disappears mid-session", async () => {
    const bridge = makeBridge(readyBootstrap(makeSession()));
    withBridge(bridge);
    const resolver = new RuntimeEndpointResolver();
    expect((await resolver.resolve()).kind).toBe("desktop");

    delete (window as unknown as Record<string, unknown>)["novelMindDesktop"];
    await expect(resolver.resolve()).resolves.toEqual({
      kind: "browser",
      endpoints: { apiBaseUrl: "/api", agentBaseUrl: "" },
    });
  });
});
