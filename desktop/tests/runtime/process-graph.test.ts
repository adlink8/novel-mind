/**
 * Process-graph + port-allocator + readiness suites (plan 43-02, Task 1/3).
 *
 * Pure unit tests — no real OS processes. Covers:
 * - ProcessGraph analysis: exact dependency order, cycle detection, transitive
 *   restart cascades, fail-closed validation,
 * - port allocation: OS dynamic loopback ports, fixed-port rejection, the
 *   distinct-port pool,
 * - readiness: per-component protocol probes (chroma heartbeat, agent healthz,
 *   next /, PostgreSQL SQL roundtrip, FastAPI dependency-chain + health),
 * - GraphSupervisor end-to-end over the DevelopmentProcessAdapter with injected
 *   FakeOps + fake transports: strict dependency order, dependency-chain gating,
 *   bounded readiness timeouts, and never-ready after a failed mandatory
 *   dependency.
 */
import { expect, test } from "@playwright/test";
import type { ComponentEndpoint, RuntimeComponent } from "../../src/runtime/types";
import { COMPONENT_DEPENDENCIES, RUNTIME_START_ORDER } from "../../src/runtime/types";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import {
  checkComponentReadiness,
  deadTransport,
  waitForReadiness,
  type DependencyEndpoints,
  type ReadinessTransport,
} from "../../src/runtime/readiness";
import {
  GraphError,
  GraphSupervisor,
  ProcessGraph,
} from "../../src/runtime/process-graph";
import {
  allocateLoopbackPort,
  assertDynamicPort,
  isDynamicPort,
  PortAllocationError,
  PortPool,
} from "../../src/runtime/port-allocator";
import { createFakeOps, type FakeOps } from "./fake-process-ops";

const BUDGETS = { startTimeoutMs: 150, drainMs: 20, killMs: 20 };

/** Fake readiness transport: records requested paths and answers per-config. */
function createFakeTransport(
  config: {
    http?: number | null;
    httpByPath?: Record<string, number | null>;
    postgres?: boolean;
  } = {},
): { transport: ReadinessTransport; httpPaths: string[] } {
  const httpPaths: string[] = [];
  return {
    httpPaths,
    transport: {
      httpStatus: async (_host, _port, path) => {
        httpPaths.push(path);
        if (config.httpByPath !== undefined && path in config.httpByPath) {
          return config.httpByPath[path] ?? null;
        }
        return config.http ?? 200;
      },
      postgresReady: async () => config.postgres ?? true,
    },
  };
}

function endpointsOf(ids: readonly RuntimeComponent[]): DependencyEndpoints {
  const map = new Map<RuntimeComponent, ComponentEndpoint>();
  for (let i = 0; i < ids.length; i += 1) {
    const id = ids[i];
    if (id !== undefined) map.set(id, { host: "127.0.0.1", port: 40000 + i });
  }
  return map;
}

test.describe("ProcessGraph analysis", () => {
  test("topological order matches the canonical dependency order", () => {
    const graph = new ProcessGraph();
    expect([...graph.order]).toEqual([...RUNTIME_START_ORDER]);
  });

  test("canonical graph validates clean", () => {
    const graph = new ProcessGraph();
    const validation = graph.validate();
    expect(validation.ok).toBe(true);
    expect(validation.cycles).toHaveLength(0);
    expect(validation.unknown).toHaveLength(0);
    expect(validation.missing).toHaveLength(0);
  });

  test("a dependency cycle is detected and rejected before any start", () => {
    const cyclic = {
      postgres_pgvector: ["next"],
      next: ["fastapi"],
      fastapi: ["agent_service"],
      agent_service: ["vector_store"],
      vector_store: ["fastapi"], // cycle: fastapi -> agent_service -> vector_store -> fastapi
    } as const;
    const graph = new ProcessGraph(
      [...RUNTIME_START_ORDER],
      cyclic as typeof COMPONENT_DEPENDENCIES,
    );
    expect(graph.cycles().length).toBeGreaterThan(0);
    expect(graph.validate().ok).toBe(false);
    expect(() => graph.topologicalOrder()).toThrow(GraphError);
  });

  test("an unknown dependency makes the graph invalid", () => {
    const bad: Record<RuntimeComponent, readonly RuntimeComponent[]> = {
      ...COMPONENT_DEPENDENCIES,
      next: ["fastapi", "bogus" as RuntimeComponent],
    };
    const graph = new ProcessGraph([...RUNTIME_START_ORDER], bad);
    const validation = graph.validate();
    expect(validation.ok).toBe(false);
    expect(validation.unknown).toContain("bogus");
  });

  test("a missing required component makes the graph invalid", () => {
    const withoutVector = [...RUNTIME_START_ORDER].filter(
      (id) => id !== "vector_store",
    );
    const graph = new ProcessGraph(withoutVector, COMPONENT_DEPENDENCIES);
    const validation = graph.validate();
    expect(validation.ok).toBe(false);
    expect(validation.missing).toContain("vector_store");
  });

  test("transitive dependents of a restart target form the cascade set", () => {
    const graph = new ProcessGraph();
    expect([...graph.affectedComponents("fastapi")]).toEqual([
      "fastapi",
      "agent_service",
      "next",
    ]);
    expect([...graph.affectedComponents("next")]).toEqual(["next"]);
    expect([...graph.affectedComponents("vector_store")]).toEqual([
      "vector_store",
      "fastapi",
      "agent_service",
      "next",
    ]);
  });

  test("dependency satisfaction is exact", () => {
    const graph = new ProcessGraph();
    expect(graph.isSatisfied("fastapi", new Set(["postgres_pgvector", "vector_store"]))).toBe(
      true,
    );
    expect(graph.isSatisfied("fastapi", new Set(["postgres_pgvector"]))).toBe(false);
    expect(graph.isSatisfied("next", new Set(["fastapi", "agent_service"]))).toBe(true);
  });
});

test.describe("port allocation", () => {
  test("allocateLoopbackPort returns a real loopback port", async () => {
    const port = await allocateLoopbackPort();
    expect(port).toBeGreaterThan(0);
    expect(port).toBeLessThanOrEqual(65535);
  });

  test("dynamic port semantics: 0 = OS allocation, fixed ports rejected", () => {
    expect(isDynamicPort(0)).toBe(true);
    expect(isDynamicPort(8001)).toBe(false);
    expect(() => assertDynamicPort(0)).not.toThrow();
    expect(() => assertDynamicPort(8001)).toThrow(PortAllocationError);
    expect(() => assertDynamicPort(-1)).toThrow(PortAllocationError);
  });

  test("PortPool allocates mutually distinct ports and tracks ownership", async () => {
    const pool = new PortPool();
    const ports = await pool.allocateMany(5);
    expect(ports).toHaveLength(5);
    expect(new Set(ports).size).toBe(5);
    for (const port of ports) {
      expect(pool.owns(port)).toBe(true);
      expect(port).toBeGreaterThan(0);
    }
    pool.release(ports[0]!);
    expect(pool.owns(ports[0]!)).toBe(false);
    expect(pool.owns(ports[1]!)).toBe(true);
  });
});

test.describe("component-specific readiness probes", () => {
  test("vector_store probes /api/v2/heartbeat and only 200 is ready", async () => {
    const { transport, httpPaths } = createFakeTransport({ httpByPath: { "/api/v2/heartbeat": 200 } });
    const ok = await checkComponentReadiness("vector_store", { host: "127.0.0.1", port: 41000 }, new Map(), transport);
    expect(ok).toBe(true);
    expect(httpPaths).toContain("/api/v2/heartbeat");

    const failing = await checkComponentReadiness("vector_store", { host: "127.0.0.1", port: 41000 }, new Map(), createFakeTransport({ httpByPath: { "/api/v2/heartbeat": 503 } }).transport);
    expect(failing).toBe(false);
  });

  test("agent_service probes /healthz and next probes /", async () => {
    const agent = createFakeTransport();
    const agentOk = await checkComponentReadiness("agent_service", { host: "127.0.0.1", port: 41001 }, new Map(), agent.transport);
    expect(agentOk).toBe(true);
    expect(agent.httpPaths).toContain("/healthz");

    const next = createFakeTransport();
    const nextOk = await checkComponentReadiness("next", { host: "127.0.0.1", port: 41002 }, new Map(), next.transport);
    expect(nextOk).toBe(true);
    expect(next.httpPaths).toContain("/");
  });

  test("postgres_pgvector requires a real SQL roundtrip, not a port", async () => {
    let seen: { host: string; port: number; user: string; database: string; query: string } | null = null;
    const transport: ReadinessTransport = {
      httpStatus: async () => 200,
      postgresReady: async (options) => {
        seen = { host: options.host, port: options.port, user: options.user, database: options.database, query: options.query };
        return true;
      },
    };
    const ok = await checkComponentReadiness("postgres_pgvector", { host: "127.0.0.1", port: 41003 }, new Map(), transport);
    expect(ok).toBe(true);
    expect(seen).toEqual({ host: "127.0.0.1", port: 41003, user: "novelmind", database: "novelmind", query: "SELECT 1" });
  });

  test("fastapi is gated on its dependency chain before /api/health", async () => {
    const endpoint: ComponentEndpoint = { host: "127.0.0.1", port: 41004 };
    const deps = endpointsOf(["postgres_pgvector", "vector_store"]);

    const withDeps = createFakeTransport({ httpByPath: { "/api/health": 200 } });
    expect(await checkComponentReadiness("fastapi", endpoint, deps, withDeps.transport)).toBe(true);
    expect(withDeps.httpPaths).toContain("/api/health");

    // Without the dependency chain the health check must NOT count as ready.
    const noDeps = createFakeTransport({ httpByPath: { "/api/health": 200 } });
    expect(await checkComponentReadiness("fastapi", endpoint, new Map(), noDeps.transport)).toBe(false);
    expect(noDeps.httpPaths).not.toContain("/api/health");

    // 503 health while the chain is present is still not ready.
    const sick = createFakeTransport({ httpByPath: { "/api/health": 503 } });
    expect(await checkComponentReadiness("fastapi", endpoint, deps, sick.transport)).toBe(false);
  });

  test("waitForReadiness is bounded and never hangs on a dead transport", async () => {
    const started = Date.now();
    const ok = await waitForReadiness(
      "next",
      { host: "127.0.0.1", port: 41005 },
      new Map(),
      deadTransport(),
      { deadlineMs: 120, intervalMs: 10 },
    );
    expect(ok).toBe(false);
    expect(Date.now() - started).toBeLessThan(2_000);
  });
});

test.describe("GraphSupervisor over the development adapter", () => {
  function makeSupervisor(ops: FakeOps, transport: ReadinessTransport) {
    const adapter = new DevelopmentProcessAdapter(ops, BUDGETS, { repoRoot: "C:/fake-repo" });
    return { supervisor: new GraphSupervisor({ adapter, transport, budgets: BUDGETS }), adapter };
  }

  test("starts the graph strictly in dependency order to ready", async () => {
    const ops = createFakeOps();
    const { supervisor } = makeSupervisor(ops, createFakeTransport().transport);
    const result = await supervisor.start();

    expect(result.ok).toBe(true);
    expect(result.failed).toBeNull();
    expect(supervisor.isReady()).toBe(true);
    expect([...result.started]).toEqual([...RUNTIME_START_ORDER]);
    const spawnOrder = ops.spawned.map((record) =>
      `${record.command} ${record.args.join(" ")}`.replace(/\\/g, "/"),
    );
    // postgres (docker) before vector (docker); fastapi after both; agent after fastapi; next last.
    const indexOf = (marker: string) => spawnOrder.findIndex((s) => s.includes(marker));
    expect(indexOf("pgvector/pgvector")).toBeLessThan(indexOf("chromadb/chroma"));
    expect(indexOf("chromadb/chroma")).toBeLessThan(indexOf("uvicorn"));
    expect(indexOf("uvicorn")).toBeLessThan(indexOf("start.mjs"));
    expect(indexOf("start.mjs")).toBeLessThan(indexOf("next/dist/bin/next"));
    await supervisor.stop();
  });

  test("a mandatory dependency that never becomes ready blocks every dependent", async () => {
    const ops = createFakeOps();
    const failing = createFakeTransport({ postgres: false }); // PostgreSQL SQL probe never succeeds
    const { supervisor } = makeSupervisor(ops, failing.transport);

    const result = await supervisor.start();
    expect(result.ok).toBe(false);
    expect(result.failed).toBe("postgres_pgvector");
    expect(supervisor.isReady()).toBe(false);
    // Only the first component was ever spawned; nothing dependent started.
    expect(ops.spawned).toHaveLength(1);
    expect(ops.spawned[0]!.args.join(" ")).toContain("pgvector/pgvector");
  });

  test("readiness timeout is typed failed, not ready, and cleans up", async () => {
    const ops = createFakeOps();
    const { supervisor } = makeSupervisor(ops, deadTransport());
    const started = Date.now();
    const result = await supervisor.start();
    expect(Date.now() - started).toBeLessThan(3_000); // bounded
    expect(result.ok).toBe(false);
    expect(result.failed).not.toBeNull();
    expect(supervisor.isReady()).toBe(false);
    expect(ops.spawned.length).toBeGreaterThan(0); // postgres was spawned...
    expect(ops.spawned.length).toBeLessThan(2); // ...but nothing else
  });

  test("failure of a middle component stops already-started components (no orphans)", async () => {
    const ops = createFakeOps();
    const failing = createFakeTransport({
      httpByPath: { "/api/health": 200, "/api/v2/heartbeat": 200, "/": 200, "/healthz": 503 },
    });
    const { supervisor, adapter } = makeSupervisor(ops, failing.transport);

    const result = await supervisor.start();
    expect(result.ok).toBe(false);
    expect(result.failed).toBe("agent_service");
    // postgres, vector_store, fastapi started, then agent_service was spawned but
    // failed strict readiness — 4 spawns total.
    const spawned = ops.spawned.map((r) => r.process);
    expect(spawned.length).toBe(4);
    expect(ops.spawned[3]!.args.join(" ")).toContain("start.mjs");
    // ...and the supervisor stopped every started tree again (drain) — no owned
    // orphans remain, including the failed agent_service.
    expect(ops.killTreeCalls).toBe(0); // graceful drain sufficed
    for (const component of ["postgres_pgvector", "vector_store", "fastapi", "agent_service"] as const) {
      expect(adapter.isRunning(component)).toBe(false);
    }
    expect(supervisor.isReady()).toBe(false);
  });

  test("crash during startup of a component surfaces a typed failed start", async () => {
    const ops = createFakeOps();
    const { supervisor } = makeSupervisor(ops, createFakeTransport().transport);
    ops.earlyExitCode = 7; // the first spawned process exits before readiness
    const result = await supervisor.start();
    expect(result.ok).toBe(false);
    expect(result.failed).toBe("postgres_pgvector");
    expect(supervisor.isReady()).toBe(false);
  });

  test("stop is idempotent after a successful start", async () => {
    const ops = createFakeOps();
    const { supervisor } = makeSupervisor(ops, createFakeTransport().transport);
    const result = await supervisor.start();
    expect(result.ok).toBe(true);
    await supervisor.stop();
    await supervisor.stop(); // no-op
    expect(supervisor.isReady()).toBe(false);
    expect(ops.spawned.length).toBe(RUNTIME_START_ORDER.length);
  });
});
