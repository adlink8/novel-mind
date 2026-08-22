/**
 * End-to-end runtime lifecycle + process fault injection (plan 43-04, Task 2).
 *
 * "Integration" here means the real modules wired together over the injected
 * seams: `DesktopRuntime` + `DevelopmentProcessAdapter` (real launch paths,
 * probe flow, drain-then-kill ownership) over `FakeOps`, plus `RuntimeRecovery`
 * and `runtimeStatusFromSnapshot` producing the renderer-safe gate status.
 *
 * Faults injected per plan Task 2:
 * - component start failure (spawn throws)            -> failed, no empty success
 * - readiness timeout (occupied port simulation)      -> failed + typed code
 * - killed PostgreSQL / vector / backend / agent / Next -> degraded, repair
 * - targeted service restart preserves unaffected services
 * - full shutdown leaves no descendants
 *
 * Every failure is asserted through BOTH the runtime snapshot and the recovery
 * status contract: `ready` is false whenever the runtime is not ready (D-43-09),
 * the failed component and redacted error code are visible, and only bounded
 * allowlisted actions are offered (T-43-04-02).
 */
import { expect, test } from "@playwright/test";
import type { AdapterBudgets } from "../../src/runtime/types";
import {
  RUNTIME_ERROR_CODES,
  RUNTIME_START_ORDER,
} from "../../src/runtime/types";
import { DesktopRuntime } from "../../src/runtime/desktop-runtime";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import { createFakeOps, DEV_ADAPTER_SPAWN_MARKERS, type FakeOps } from "./fake-process-ops";
import type { RuntimeRecoveryState } from "../../src/shared/runtime-status";
import { RuntimeRecovery } from "../../src/runtime/recovery";

const BUDGETS: Partial<AdapterBudgets> = {
  startTimeoutMs: 400,
  drainMs: 50,
  killMs: 50,
};

interface Fixture {
  ops: FakeOps;
  runtime: DesktopRuntime;
  recovery: RuntimeRecovery;
}

function makeFixture(): Fixture {
  const ops = createFakeOps();
  const runtime = new DesktopRuntime({
    adapter: new DevelopmentProcessAdapter(ops, BUDGETS, { repoRoot: "C:/fake-repo" }),
  });
  return { ops, runtime, recovery: new RuntimeRecovery({ runtime }) };
}

function runningSpawnCount(ops: FakeOps): number {
  return ops.spawned.filter((record) => record.process.exitCode === null).length;
}

/** A failed/degraded runtime must never present as empty domain success. */
function expectHonestFailure(status: RuntimeRecoveryState, expectedCode: string): void {
  expect(status.ready).toBe(false);
  expect(status.state).not.toBe("ready");
  expect(status.errorCode).toBe(expectedCode);
  expect(status.errorMessage).not.toBeNull();
}

async function recoverToReady(fixture: Fixture): Promise<void> {
  const result = await fixture.recovery.recover("retry");
  expect(result.applied).toBe(true);
  expect(result.status.state).toBe("ready");
  expect(result.status.ready).toBe(true);
}

test.describe("runtime lifecycle through the real runtime", () => {
  test("first start to ready surfaces an honest gate status with no actions", async () => {
    const { runtime, recovery } = makeFixture();
    const snapshot = await runtime.ensureReady();
    expect(snapshot.state).toBe("ready");
    expect(snapshot.ready).toBe(true);
    expect(snapshot.lastError).toBeNull();

    const status = await recovery.status();
    expect(status.ready).toBe(true);
    expect(status.state).toBe("ready");
    expect(status.errorCode).toBeNull();
    expect(status.failedComponent).toBeNull();
    expect(status.recoveryActions).toEqual([]);
    expect(status.startedAt).not.toBeNull();
  });

  test("targeted service restart preserves unaffected services", async () => {
    const fixture = makeFixture();
    await fixture.runtime.ensureReady();
    const before = fixture.ops.spawned.length;

    const snapshot = await fixture.runtime.restart("fastapi");
    expect(snapshot.state).toBe("ready");
    // fastapi + agent_service + next recycled; postgres + vector preserved.
    expect(fixture.ops.spawned.length).toBe(before + 3);
    const status = await fixture.recovery.status();
    expect(status.ready).toBe(true);
  });

  test("full shutdown leaves no descendants and is idempotent", async () => {
    const fixture = makeFixture();
    await fixture.runtime.ensureReady();
    const report = await fixture.runtime.shutdown();
    expect(report.failed).toHaveLength(0);
    expect([...report.stopped].sort()).toEqual([...RUNTIME_START_ORDER].sort());
    expect(runningSpawnCount(fixture.ops)).toBe(0);

    const status = await fixture.recovery.status();
    expect(status.state).toBe("stopped");
    expect(status.ready).toBe(false);
    expect(status.recoveryActions.map((a) => a.id)).toEqual(["retry", "openDiagnostics"]);

    const again = await fixture.runtime.shutdown();
    expect(again.stopped).toHaveLength(0);
    expect(runningSpawnCount(fixture.ops)).toBe(0);
  });

  test("every killed component -> degraded, typed, and repairable with no orphans", async () => {
    for (const killed of RUNTIME_START_ORDER) {
      const fixture = makeFixture();
      await fixture.runtime.ensureReady();
      fixture.ops.spawnedProcess(DEV_ADAPTER_SPAWN_MARKERS[killed]).emitExit(9);
      await fixture.ops.sleep(10);

      const status = await fixture.recovery.status();
      expect(status.state).toBe("degraded");
      expect(status.ready).toBe(false);
      expect(status.failedComponent).toBe(killed);
      expect(status.errorCode).toBe(RUNTIME_ERROR_CODES.EXIT_EARLY);
      // Bounded degraded actions only.
      expect(status.recoveryActions.map((a) => a.id)).toEqual([
        "retry",
        "restart",
        "openDiagnostics",
      ]);

      // Repair returns to ready; exactly the failed component is re-spawned
      // (dependents that stayed healthy are preserved — D-43-07).
      const before = fixture.ops.spawned.length;
      await recoverToReady(fixture);
      expect(fixture.ops.spawned.length).toBe(before + 1);

      const report = await fixture.runtime.shutdown();
      expect(report.failed).toHaveLength(0);
      expect(runningSpawnCount(fixture.ops)).toBe(0);
    }
  });

  test("occupied port -> START_TIMEOUT -> failed with cleanup, never empty success", async () => {
    const fixture = makeFixture();
    const target = "fastapi";
    const targetIndex = RUNTIME_START_ORDER.indexOf(target);
    const occupiedPort = 41000 + targetIndex;
    // Everything probes fine except the "occupied" port (port-open is NOT ready).
    fixture.ops.probeResult = (_host, port) => port !== occupiedPort;

    const snapshot = await fixture.runtime.ensureReady();
    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.START_TIMEOUT);
    // Components started before the occupied port were cleaned up (reverse order).
    for (const id of ["postgres_pgvector", "vector_store"] as const) {
      const comp = snapshot.components.find((c) => c.id === id);
      expect(comp?.state).toBe("stopped");
      expect(comp?.endpoint).toBeNull();
    }
    const failed = snapshot.components.find((c) => c.id === target);
    expect(failed?.lastError?.code).toBe(RUNTIME_ERROR_CODES.START_TIMEOUT);
    expect(runningSpawnCount(fixture.ops)).toBe(0);

    const status = await fixture.recovery.status();
    expectHonestFailure(status, RUNTIME_ERROR_CODES.START_TIMEOUT);
    expect(status.failedComponent).toBe(target);

    // Port freed; retry reaches ready.
    fixture.ops.probeResult = true;
    await recoverToReady(fixture);
    expect(runningSpawnCount(fixture.ops)).toBe(5);
  });

  test("component start failure -> failed with redacted error, retry reaches ready", async () => {
    const fixture = makeFixture();
    // postgres + vector spawn fine; fastapi spawn throws.
    let spawns = 0;
    const originalSpawn = fixture.ops.spawn.bind(fixture.ops);
    fixture.ops.spawn = (command, args, options) => {
      spawns += 1;
      if (spawns === 3) throw new Error("fastapi spawn failed (injected)");
      return originalSpawn(command, args, options);
    };

    const snapshot = await fixture.runtime.ensureReady();
    expect(snapshot.state).toBe("failed");
    const failed = snapshot.components.find((c) => c.id === "fastapi");
    expect(failed?.lastError?.code).toBe(RUNTIME_ERROR_CODES.SPAWN_FAILED);
    expect(runningSpawnCount(fixture.ops)).toBe(0);

    const status = await fixture.recovery.status();
    expectHonestFailure(status, RUNTIME_ERROR_CODES.SPAWN_FAILED);
    expect(status.failedComponent).toBe("fastapi");
    expect(status.recoveryActions.map((a) => a.id)).toEqual(["retry", "openDiagnostics"]);

    // Fault clears; retry starts the full graph.
    await recoverToReady(fixture);
    expect(runningSpawnCount(fixture.ops)).toBe(5);
  });

  test("degraded recovery actions execute through the runtime state machine only", async () => {
    const fixture = makeFixture();
    await fixture.runtime.ensureReady();
    fixture.ops.spawnedProcess(DEV_ADAPTER_SPAWN_MARKERS.agent_service).emitExit(9);
    await fixture.ops.sleep(10);

    // restart targets the failed component's cascade (agent_service + next).
    const before = fixture.ops.spawned.length;
    const result = await fixture.recovery.recover("restart");
    expect(result.applied).toBe(true);
    expect(result.status.state).toBe("ready");
    expect(fixture.ops.spawned.length).toBe(before + 2);
  });

  test("renderer cannot request actions not in the current state (T-43-04-02)", async () => {
    const fixture = makeFixture();
    await fixture.runtime.ensureReady(); // ready -> no actions allowed
    const result = await fixture.recovery.recover("restart");
    expect(result.applied).toBe(false);
    expect(result.error?.code).toBe(RUNTIME_ERROR_CODES.RECOVERY_DENIED);
    expect(result.status.ready).toBe(true); // runtime untouched

    // From stopped, restoreBackup is not in the allowlist (no data managed here).
    await fixture.runtime.shutdown();
    const denied = await fixture.recovery.recover("restoreBackup");
    expect(denied.applied).toBe(false);
    expect(denied.error?.code).toBe(RUNTIME_ERROR_CODES.RECOVERY_DENIED);
  });
});
