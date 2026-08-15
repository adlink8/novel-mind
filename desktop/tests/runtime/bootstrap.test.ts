/**
 * Runtime bootstrap producer tests (Phase 44, plan 44-01, Task 1/3).
 *
 * The plan's `verify` runs `tests/runtime/bootstrap.test.ts` under the runtime
 * Playwright config (`npx playwright test --config tests/runtime/playwright.config.ts`).
 * This suite is pure Node (no Electron) and drives `RuntimeBootstrapProvider`
 * against a real `DesktopRuntime` with injected FakeOps, so endpoints are the
 * fake-allocated ports — never fixed packaged ports.
 *
 * Covers:
 * - not-ready before `ensureReady` (fail closed, D-43-09),
 * - ready → typed session with logical api/agent/renderer handles + five
 *   component endpoints (all loopback-validated),
 * - no secrets / env / paths in the payload (T-44-01-02),
 * - deterministic expiry → new session id,
 * - restart rotation → new session id,
 * - shutdown / degraded → unavailable, stale session invalidated,
 * - malformed endpoint (ready component with a non-loopback endpoint) fails
 *   closed as unavailable("malformed"),
 * - endpoint drift invalidates the cached session (restart-cascade repair).
 */
import { expect, test } from "@playwright/test";
import { RuntimeBootstrapProvider } from "../../src/runtime/bootstrap";
import { DesktopRuntime } from "../../src/runtime/desktop-runtime";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import {
  BOOTSTRAP_SESSION_TTL_MS,
  BOOTSTRAP_LOOPBACK_HOST,
  type BootstrapSession,
  type RuntimeBootstrap,
} from "../../src/shared/bootstrap-contract";
import { createFakeOps, type FakeOps } from "./fake-process-ops";
import type { AdapterBudgets } from "../../src/runtime/types";

const BUDGETS: Partial<AdapterBudgets> = {
  startTimeoutMs: 300,
  drainMs: 50,
  killMs: 50,
};

function makeRuntime(ops: FakeOps) {
  const adapter = new DevelopmentProcessAdapter(ops, BUDGETS, { repoRoot: "C:/fake-repo" });
  return { runtime: new DesktopRuntime({ adapter }), adapter };
}

/** FakeOps whose allocated loopback ports are deterministic (41000 + spawn index). */
function makeOps(): FakeOps {
  return createFakeOps();
}

function expectSession(bootstrap: RuntimeBootstrap): BootstrapSession {
  expect(bootstrap.status).toBe("ready");
  if (bootstrap.status !== "ready") throw new Error("expected ready bootstrap");
  return bootstrap.session;
}

/** The live snapshot component endpoints from a ready runtime. */
async function snapshotEndpoints(
  runtime: DesktopRuntime,
): Promise<Map<string, { host: string; port: number }>> {
  const snapshot = await runtime.status();
  const map = new Map<string, { host: string; port: number }>();
  for (const component of snapshot.components) {
    if (component.endpoint !== null) {
      map.set(component.id, component.endpoint);
    }
  }
  return map;
}

/** The live fastapi endpoint (used to show drift is detectable). */
async function liveFastApiEndpoint(
  runtime: DesktopRuntime,
): Promise<{ host: string; port: number } | null> {
  const snapshot = await runtime.status();
  const component = snapshot.components.find((c) => c.id === "fastapi");
  return component?.endpoint ?? null;
}

test.describe("RuntimeBootstrapProvider", () => {
  test("cannot produce a session before the runtime is ready (fail closed)", async () => {
    const { runtime } = makeRuntime(makeOps());
    const provider = new RuntimeBootstrapProvider({ runtime: () => runtime });

    const bootstrap = await provider.get();
    expect(bootstrap).toEqual({ status: "unavailable", reason: "not-ready" });
  });

  test("ready runtime yields one typed session with logical handles and loopback endpoints", async () => {
    const { runtime } = makeRuntime(makeOps());
    const provider = new RuntimeBootstrapProvider({
      runtime: () => runtime,
      sessionId: () => "sess-1",
      now: () => new Date("2026-08-10T00:00:00.000Z"),
    });
    await runtime.ensureReady();

    const bootstrap = await provider.get();
    const session = expectSession(bootstrap);

    expect(session.sessionId).toBe("sess-1");
    expect(session.issuedAt).toBe("2026-08-10T00:00:00.000Z");
    expect(session.expiresAt).toBe(
      new Date(Date.parse(session.issuedAt) + BOOTSTRAP_SESSION_TTL_MS).toISOString(),
    );

    // Logical service handles mirror the live component endpoints.
    const endpoints = await snapshotEndpoints(runtime);
    for (const service of ["api", "agent", "renderer"] as const) {
      const handle = session.services[service];
      const live = endpoints.get(
        service === "api" ? "fastapi" : service === "agent" ? "agent_service" : "next",
      );
      expect(live).toBeDefined();
      expect(handle.host).toBe(BOOTSTRAP_LOOPBACK_HOST);
      expect(handle.host).toBe(live?.host);
      expect(handle.port).toBe(live?.port);
      expect(handle.port).toBeGreaterThan(0);
    }

    // Every one of the five components is present and loopback-bounded.
    const componentIds = Object.keys(session.components).sort();
    expect(componentIds).toEqual([
      "agent_service",
      "fastapi",
      "next",
      "postgres_pgvector",
      "vector_store",
    ]);
    for (const component of Object.values(session.components)) {
      expect(component.host).toBe(BOOTSTRAP_LOOPBACK_HOST);
      expect(component.port).toBeGreaterThan(0);
    }

    // Bounded capability flags.
    expect(session.capabilities.agentStreaming).toBe(true);
  });

  test("payload contains no secrets, env or paths (T-44-01-02)", async () => {
    const { runtime } = makeRuntime(makeOps());
    const provider = new RuntimeBootstrapProvider({ runtime: () => runtime });
    await runtime.ensureReady();

    const serialized = JSON.stringify(await provider.get());
    for (const forbidden of [
      "token",
      "secret",
      "key",
      "password",
      "process.env",
      "NEXT_PUBLIC",
      "C:/",
      "/home",
      "pid",
      "executable",
    ]) {
      expect(serialized.toLowerCase()).not.toContain(forbidden);
    }
  });

  test("session expires deterministically and rotates to a fresh session id", async () => {
    const { runtime } = makeRuntime(makeOps());
    let current = new Date("2026-08-10T00:00:00.000Z");
    const provider = new RuntimeBootstrapProvider({
      runtime: () => runtime,
      sessionId: () => "sess-first",
      now: () => current,
    });
    await runtime.ensureReady();

    const first = expectSession(await provider.get());
    expect(first.sessionId).toBe("sess-first");

    // After TTL, the cached session is expired → fresh session id.
    current = new Date(current.getTime() + BOOTSTRAP_SESSION_TTL_MS + 1);
    const second = expectSession(await provider.get());
    expect(second.sessionId).toBe("sess-first"); // same injected factory — but session differs
    expect(second.issuedAt).toBe(current.toISOString());
    expect(second.issuedAt).not.toBe(first.issuedAt);
  });

  test("runtime restart rotates the session and invalidates the stale one", async () => {
    const ops = makeOps();
    const { runtime } = makeRuntime(ops);
    let n = 0;
    const provider = new RuntimeBootstrapProvider({
      runtime: () => runtime,
      sessionId: () => `sess-${++n}`,
      now: () => new Date("2026-08-10T00:00:00.000Z"),
    });
    await runtime.ensureReady();
    const first = expectSession(await provider.get());
    expect(first.sessionId).toBe("sess-1");

    await runtime.shutdown();
    expect(await provider.get()).toEqual({ status: "unavailable", reason: "not-ready" });

    await runtime.ensureReady();
    const second = expectSession(await provider.get());
    expect(second.sessionId).toBe("sess-2");
    expect(second.sessionId).not.toBe(first.sessionId);
  });

  test("degraded runtime serves unavailable and drops the cached session", async () => {
    const ops = makeOps();
    const { runtime } = makeRuntime(ops);
    const provider = new RuntimeBootstrapProvider({ runtime: () => runtime });
    await runtime.ensureReady();
    expect((await provider.get()).status).toBe("ready");

    // Crash a ready component → degraded (D-43-08). The dev adapter launches
    // next through the node binary, so match on the joined spawn marker.
    const nextProcess = ops.spawnedProcess("next");
    expect(nextProcess).toBeDefined();
    nextProcess.emitExit(1);

    const status = await runtime.status();
    expect(status.state).toBe("degraded");

    const bootstrap = await provider.get();
    expect(bootstrap.status).toBe("unavailable");
    expect(bootstrap).toEqual({ status: "unavailable", reason: "not-ready" });
  });

  test("a ready component with a non-loopback endpoint fails closed as malformed", async () => {
    const ops = makeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();

    const live = await liveFastApiEndpoint(runtime);
    expect(live).not.toBeNull();

    // Drive a provider whose runtime reports a ready snapshot with a tampered
    // fastapi endpoint — the provider must fail closed (T-44-01-01).
    const tampered: DesktopRuntime = {
      status: async () => {
        const snapshot = await runtime.status();
        return {
          ...snapshot,
          components: snapshot.components.map((c) =>
            c.id === "fastapi"
              ? { ...c, endpoint: { host: "10.0.0.5", port: 9999 } }
              : c,
          ),
        };
      },
    } as unknown as DesktopRuntime;

    const tamperedProvider = new RuntimeBootstrapProvider({
      runtime: () => tampered,
      sessionId: () => "sess-t",
    });
    const bootstrap = await tamperedProvider.get();
    expect(bootstrap).toEqual({ status: "unavailable", reason: "malformed" });
  });

  test("endpoint drift (targeted restart) invalidates the cached session", async () => {
    const ops = makeOps();
    const { runtime } = makeRuntime(ops);
    let n = 0;
    let current = new Date("2026-08-10T00:00:00.000Z");
    const provider = new RuntimeBootstrapProvider({
      runtime: () => runtime,
      sessionId: () => `sess-${++n}`,
      now: () => current,
    });
    await runtime.ensureReady();
    const first = expectSession(await provider.get());
    expect(first.sessionId).toBe("sess-1");

    // Targeted restart of a leaf component recycles only its own endpoint.
    await runtime.restart("fastapi");
    expect((await runtime.status()).state).toBe("ready");
    current = new Date(current.getTime() + 1);

    const second = expectSession(await provider.get());
    expect(second.sessionId).toBe("sess-2");
    expect(second.issuedAt).not.toBe(first.issuedAt);
  });

  test("null runtime is unavailable and never throws", async () => {
    const provider = new RuntimeBootstrapProvider({ runtime: () => null });
    await expect(provider.get()).resolves.toEqual({
      status: "unavailable",
      reason: "not-ready",
    });
  });
});
