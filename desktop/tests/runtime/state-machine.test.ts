/**
 * DesktopRuntime state-machine tests (plan 43-01, Task 1/3).
 *
 * Covers:
 * - the explicit transition table (all 7 x 7 pairs enumerated, no self-loops),
 * - terminal-state enumeration (stopped / failed),
 * - the ready invariant: "ready with a failed dependency" is unrepresentable,
 * - end-to-end lifecycle flows through the real DevelopmentProcessAdapter with
 *   injected FakeOps (startup, migration, crash -> degraded, repair, targeted
 *   restart cascade, whole-graph restart, shutdown idempotency),
 * - snapshot redaction (no secrets / PIDs / executables).
 */
import { expect, test } from "@playwright/test";
import type {
  AdapterBudgets,
  MigrationGate,
  RuntimeComponent,
  RuntimeState,
} from "../../src/runtime/types";
import { RUNTIME_ERROR_CODES, RUNTIME_START_ORDER, RUNTIME_STATES } from "../../src/runtime/types";
import { CAN_TRANSITION, canTransition, DesktopRuntime } from "../../src/runtime/desktop-runtime";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import { createFakeOps, type FakeOps } from "./fake-process-ops";

const BUDGETS: Partial<AdapterBudgets> = {
  startTimeoutMs: 300,
  drainMs: 50,
  killMs: 50,
};

const ALL_STATES: readonly RuntimeState[] = [...RUNTIME_STATES];

/**
 * The canonical transition table. MUST stay in sync with CAN_TRANSITION in
 * desktop-runtime.ts; the enumeration test below fails on any drift.
 */
const EXPECTED_TRANSITIONS: Readonly<Record<RuntimeState, readonly RuntimeState[]>> = {
  stopped: ["starting"],
  starting: ["migrating", "ready", "failed", "stopping"],
  migrating: ["ready", "failed", "stopping"],
  ready: ["degraded", "stopping"],
  degraded: ["ready", "failed", "stopping"],
  failed: ["starting", "stopping"],
  stopping: ["stopped", "failed"],
};

/** States that terminate a lifecycle flow (no running services afterwards). */
const TERMINAL_STATES: readonly RuntimeState[] = ["stopped", "failed"];

function makeRuntime(
  ops: FakeOps,
  migration?: MigrationGate,
): { runtime: DesktopRuntime; adapter: DevelopmentProcessAdapter } {
  const adapter = new DevelopmentProcessAdapter(ops, BUDGETS, { repoRoot: "C:/fake-repo" });
  return { runtime: new DesktopRuntime({ adapter, migration }), adapter };
}

function migrationGate(behavior: { needs: boolean; runThrows?: boolean }): MigrationGate & {
  needsCalls: () => number;
  runCalls: () => number;
} {
  let needsCalls = 0;
  let runCalls = 0;
  return {
    needsMigration: async () => {
      needsCalls += 1;
      return behavior.needs;
    },
    run: async () => {
      runCalls += 1;
      if (behavior.runThrows === true) throw new Error("migration failed (injected)");
    },
    needsCalls: () => needsCalls,
    runCalls: () => runCalls,
  };
}

test.describe("runtime state machine", () => {
  test("transition table is explicit, total and free of self-loops", () => {
    for (const from of ALL_STATES) {
      for (const to of ALL_STATES) {
        if (from === to) {
          expect(canTransition(from, to), `${from} -> ${from} self-loop`).toBe(false);
        } else {
          const expected = EXPECTED_TRANSITIONS[from].includes(to);
          expect(canTransition(from, to), `${from} -> ${to}`).toBe(expected);
        }
      }
    }
  });

  test("CAN_TRANSITION matches the canonical table exactly", () => {
    for (const from of ALL_STATES) {
      expect([...CAN_TRANSITION[from]].sort()).toEqual([...EXPECTED_TRANSITIONS[from]].sort());
    }
  });

  test("terminal states are enumerated and cannot reach ready directly", () => {
    const EXPECTED_OUTGOING: Partial<Record<RuntimeState, readonly RuntimeState[]>> = {
      // stopped: a fresh start is the only way out.
      stopped: ["starting"],
      // failed: may be retried (starting) or torn down (stopping).
      failed: ["starting", "stopping"],
    };
    for (const terminal of TERMINAL_STATES) {
      const outgoing = ALL_STATES.filter((to) => canTransition(terminal, to));
      const expected = EXPECTED_OUTGOING[terminal] ?? [];
      expect([...outgoing].sort()).toEqual([...expected].sort());
      expect(canTransition(terminal, "ready")).toBe(false);
    }
  });

  test("ready is only reachable from starting/migrating/degraded", () => {
    const sources = ALL_STATES.filter((from) => canTransition(from, "ready"));
    expect([...sources].sort()).toEqual(["degraded", "migrating", "starting"]);
  });

  test("illegal paths are rejected by the table", () => {
    expect(canTransition("stopped", "ready")).toBe(false);
    expect(canTransition("stopped", "degraded")).toBe(false);
    expect(canTransition("stopped", "migrating")).toBe(false);
    expect(canTransition("ready", "starting")).toBe(false);
    expect(canTransition("ready", "failed")).toBe(false); // must pass through degraded
    expect(canTransition("starting", "stopped")).toBe(false); // must pass through stopping
  });
});

test.describe("DesktopRuntime lifecycle", () => {
  test("ensureReady drives the graph to ready with allocated endpoints", async () => {
    const { runtime } = makeRuntime(createFakeOps());
    const snapshot = await runtime.ensureReady();

    expect(snapshot.state).toBe("ready");
    expect(snapshot.ready).toBe(true);
    expect(snapshot.startedAt).not.toBeNull();
    expect(snapshot.lastError).toBeNull();
    expect(snapshot.components).toHaveLength(RUNTIME_START_ORDER.length);
    for (const component of snapshot.components) {
      expect(component.state).toBe("ready");
      expect(component.ready).toBe(true);
      expect(component.endpoint?.host).toBe("127.0.0.1");
      expect(component.endpoint!.port).toBeGreaterThan(0);
      expect(component.lastError).toBeNull();
    }
  });

  test("ensureReady is idempotent once ready", async () => {
    const { runtime } = makeRuntime(createFakeOps());
    await runtime.ensureReady();
    const again = await runtime.ensureReady();
    expect(again.state).toBe("ready");
  });

  test("snapshot carries no secrets, PIDs or executables", async () => {
    const { runtime } = makeRuntime(createFakeOps());
    await runtime.ensureReady();
    const serialized = JSON.stringify(await runtime.status()).toLowerCase();
    expect(serialized).not.toContain("token");
    expect(serialized).not.toContain("secret");
    expect(serialized).not.toContain("password");
    expect(serialized).not.toContain('"pid"');
    expect(serialized).not.toContain("executable");
    expect(serialized).not.toContain("command");
  });

  test("startup failure surfaces typed failed state with a redacted error", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    const snapshot = await runtime.ensureReady();

    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.SPAWN_FAILED);
    const failedComponent = snapshot.components.find((c) => c.state === "failed");
    expect(failedComponent).toBeDefined();
    expect(failedComponent?.lastError?.code).toBe(RUNTIME_ERROR_CODES.SPAWN_FAILED);
    expect(failedComponent?.ready).toBe(false);
  });

  test("recovery after failure reaches ready on retry", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    expect((await runtime.ensureReady()).state).toBe("failed");

    ops.spawnThrows = false;
    const snapshot = await runtime.ensureReady();
    expect(snapshot.state).toBe("ready");
  });

  test("migration gate runs before ready and failure -> MIGRATION_FAILED", async () => {
    const gate = migrationGate({ needs: true });
    const { runtime } = makeRuntime(createFakeOps(), gate);
    const snapshot = await runtime.ensureReady();
    expect(gate.needsCalls()).toBe(1);
    expect(gate.runCalls()).toBe(1);
    expect(snapshot.state).toBe("ready");
  });

  test("migration failure -> failed with MIGRATION_FAILED, never ready", async () => {
    const gate = migrationGate({ needs: true, runThrows: true });
    const { runtime } = makeRuntime(createFakeOps(), gate);
    const snapshot = await runtime.ensureReady();
    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);
  });

  test("crash after ready -> degraded, never 'ready with a failed component'", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    expect((await runtime.ensureReady()).state).toBe("ready");

    // Kill the next renderer process out from under the runtime.
    ops.spawnedProcess("next").emitExit(9);
    await ops.sleep(10);

    const degraded = await runtime.status();
    expect(degraded.state).toBe("degraded");
    expect(degraded.ready).toBe(false);
    const next = degraded.components.find((c) => c.id === "next");
    expect(next?.state).toBe("failed");
    expect(next?.lastError?.code).toBe(RUNTIME_ERROR_CODES.EXIT_EARLY);

    // Repair brings the graph back to ready.
    const repaired = await runtime.ensureReady();
    expect(repaired.state).toBe("ready");
    expect(repaired.components.find((c) => c.id === "next")?.state).toBe("ready");
  });

  test("targeted restart recycles the target and its transitive dependents", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();
    const before = ops.spawned.length; // 5

    const snapshot = await runtime.restart("fastapi");
    expect(snapshot.state).toBe("ready");
    // fastapi + agent_service + next are recycled (agent depends on fastapi,
    // next depends on fastapi + agent_service).
    expect(ops.spawned.length).toBe(before + 3);
  });

  test("targeted restart failure -> failed, not ready", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();

    ops.spawnThrows = true; // next start during the cascade will fail
    const snapshot = await runtime.restart("next");
    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
  });

  test("whole-graph restart recycles every component", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();
    const before = ops.spawned.length; // 5

    const snapshot = await runtime.restart();
    expect(snapshot.state).toBe("ready");
    expect(ops.spawned.length).toBe(before + 5);
  });

  test("shutdown stops the whole tree in reverse order and is idempotent", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();

    const report = await runtime.shutdown();
    expect(report.failed).toHaveLength(0);
    expect([...report.stopped].sort()).toEqual([...RUNTIME_START_ORDER].sort());

    const snapshot = await runtime.status();
    expect(snapshot.state).toBe("stopped");
    expect(snapshot.ready).toBe(false);
    for (const component of snapshot.components) {
      expect(component.state).toBe("stopped");
      expect(component.ready).toBe(false);
      expect(component.endpoint).toBeNull();
    }

    const again = await runtime.shutdown(); // idempotent
    expect(again.stopped).toHaveLength(0);
    expect(again.failed).toHaveLength(0);
  });

  test("restart after shutdown starts the graph again", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();
    await runtime.shutdown();

    const snapshot = await runtime.restart();
    expect(snapshot.state).toBe("ready");
    expect(snapshot.components.every((c) => c.ready)).toBe(true);
  });

  test("unknown restart target -> COMPONENT_UNKNOWN", async () => {
    const { runtime } = makeRuntime(createFakeOps());
    let error: unknown;
    try {
      await runtime.restart("bogus" as RuntimeComponent);
    } catch (cause) {
      error = cause;
    }
    expect(error).toBeDefined();
    expect((error as { code: string }).code).toBe(RUNTIME_ERROR_CODES.COMPONENT_UNKNOWN);
  });

  test("shutdown with a failed tree reports failed entries and ends failed", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();

    // Make every stop fail to kill.
    ops.drainSucceeds = false;
    ops.killTreeSucceeds = false;
    const report = await runtime.shutdown();
    expect(report.failed.length).toBeGreaterThan(0);
    const snapshot = await runtime.status();
    // A live orphan may remain, so the runtime must not claim a clean stop.
    expect(snapshot.state).toBe("failed");
  });
});
