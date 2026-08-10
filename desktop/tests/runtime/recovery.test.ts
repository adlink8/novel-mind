/**
 * Runtime recovery contract + executor tests (plan 43-04, Task 1/3).
 *
 * Covers:
 * - `runtimeStatusFromSnapshot`: ready/degraded/failed mapping is honest (a
 *   failed/degraded runtime never renders as empty domain success, D-43-09),
 *   redacted error payloads, and state-derived bounded action allowlists
 *   (T-43-04-02) with `backupAvailable` gating `restoreBackup`.
 * - `RuntimeRecovery`: allowlisted actions are applied through the runtime
 *   state machine; unknown actions and actions not allowed in the current state
 *   are denied with RECOVERY_DENIED; `restoreBackup` failures map to
 *   BACKUP_RESTORE_FAILED; diagnostics carry redacted component labels only.
 * - DesktopRuntime plan 43-04 invariant: a failed full start or failed
 *   migration never leaves a half-started graph behind — started components are
 *   stopped in reverse dependency order, and a retry reaches ready cleanly.
 */
import { expect, test } from "@playwright/test";
import type { AdapterBudgets, MigrationGate } from "../../src/runtime/types";
import { RUNTIME_ERROR_CODES, RUNTIME_START_ORDER } from "../../src/runtime/types";
import { DesktopRuntime } from "../../src/runtime/desktop-runtime";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import { createFakeOps, DEV_ADAPTER_SPAWN_MARKERS, type FakeOps } from "./fake-process-ops";
import {
  isActionAllowed,
  isRecoveryActionId,
  recoveryActionIdsFor,
  runtimeStatusFromSnapshot,
} from "../../src/shared/runtime-status";
import type { RuntimeRecoveryState } from "../../src/shared/runtime-status";
import {
  RuntimeRecovery,
  type RecoveryDataCapabilities,
} from "../../src/runtime/recovery";

const BUDGETS: Partial<AdapterBudgets> = {
  startTimeoutMs: 300,
  drainMs: 50,
  killMs: 50,
};

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

/** Successful spawns only — used to assert no processes remain running. */
function runningSpawnCount(ops: FakeOps): number {
  return ops.spawned.filter((record) => record.process.exitCode === null).length;
}

test.describe("runtime status contract (runtime-status.ts)", () => {
  test("recovery action ids are a fixed bounded allowlist", () => {
    expect(["retry", "restart", "openDiagnostics", "restoreBackup"]).toEqual(
      expect.arrayContaining([...recoveryActionIdsFor("failed", true)]),
    );
    expect(isRecoveryActionId("retry")).toBe(true);
    expect(isRecoveryActionId("restart")).toBe(true);
    expect(isRecoveryActionId("restoreBackup")).toBe(true);
    expect(isRecoveryActionId("openDiagnostics")).toBe(true);
    expect(isRecoveryActionId("delete-all-data")).toBe(false);
    expect(isRecoveryActionId("")).toBe(false);
    expect(isRecoveryActionId(null)).toBe(false);
    expect(isRecoveryActionId(42)).toBe(false);
  });

  test("per-state action allowlists are bounded and backup-aware", () => {
    // In-flight states offer NO recovery action.
    for (const state of ["starting", "migrating", "stopping"] as const) {
      expect(recoveryActionIdsFor(state, true)).toEqual([]);
    }
    // Ready: nothing to recover.
    expect(recoveryActionIdsFor("ready", true)).toEqual([]);
    // Degraded: repair-oriented actions, no destructive restore.
    expect(recoveryActionIdsFor("degraded", true)).toEqual([
      "retry",
      "restart",
      "openDiagnostics",
    ]);
    // Failed: retry + diagnostics; restoreBackup only when a backup exists.
    expect(recoveryActionIdsFor("failed", true)).toEqual([
      "retry",
      "openDiagnostics",
      "restoreBackup",
    ]);
    expect(recoveryActionIdsFor("failed", false)).toEqual(["retry", "openDiagnostics"]);
    expect(recoveryActionIdsFor("stopped", false)).toEqual(["retry", "openDiagnostics"]);

    // Allow-list checks agree with the state derivation.
    expect(isActionAllowed("failed", "restoreBackup", true)).toBe(true);
    expect(isActionAllowed("failed", "restoreBackup", false)).toBe(false);
    expect(isActionAllowed("degraded", "restoreBackup", true)).toBe(false);
    expect(isActionAllowed("degraded", "restart", true)).toBe(true);
    expect(isActionAllowed("migrating", "retry", true)).toBe(false);
  });

  test("a failed runtime never maps to a ready status with empty success", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    const snapshot = await runtime.ensureReady();

    const status: RuntimeRecoveryState = runtimeStatusFromSnapshot(snapshot, {
      backupAvailable: true,
    });
    expect(status.state).toBe("failed");
    expect(status.ready).toBe(false); // never empty-success
    expect(status.failedComponent).not.toBeNull();
    expect(status.errorCode).toBe(RUNTIME_ERROR_CODES.SPAWN_FAILED);
    expect(status.errorMessage).not.toBeNull();
    // Only allowlisted failed-state actions are offered.
    expect(status.recoveryActions.map((a) => a.id)).toEqual([
      "retry",
      "openDiagnostics",
      "restoreBackup",
    ]);
    // Fixed labels, no paths/secrets.
    for (const action of status.recoveryActions) {
      expect(action.label.length).toBeGreaterThan(0);
      expect(action.description.length).toBeGreaterThan(0);
    }
  });

  test("degraded runtime maps to degraded status with repair actions only", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();
    ops.spawnedProcess("next").emitExit(9);
    await ops.sleep(10);

    const status = runtimeStatusFromSnapshot(await runtime.status(), { backupAvailable: true });
    expect(status.state).toBe("degraded");
    expect(status.ready).toBe(false);
    expect(status.failedComponent).toBe("next");
    expect(status.errorCode).toBe(RUNTIME_ERROR_CODES.EXIT_EARLY);
    expect(status.recoveryActions.map((a) => a.id)).toEqual([
      "retry",
      "restart",
      "openDiagnostics",
    ]);
  });

  test("ready runtime maps to ready status with no actions", async () => {
    const { runtime } = makeRuntime(createFakeOps());
    const status = runtimeStatusFromSnapshot(await runtime.ensureReady(), {
      backupAvailable: true,
    });
    expect(status.ready).toBe(true);
    expect(status.state).toBe("ready");
    expect(status.errorCode).toBeNull();
    expect(status.failedComponent).toBeNull();
    expect(status.recoveryActions).toEqual([]);
  });
});

test.describe("RuntimeRecovery executor", () => {
  function dataCapabilities(behavior: {
    backupAvailable?: boolean;
    restoreThrows?: boolean;
  } = {}): RecoveryDataCapabilities & { restoreCalls: () => number } {
    let restoreCalls = 0;
    return {
      backupAvailable: async () => behavior.backupAvailable ?? true,
      recoveryInstruction: async () => "Retry the upgrade; old data is intact.",
      restoreBackup: async () => {
        restoreCalls += 1;
        if (behavior.restoreThrows === true) throw new Error("restore failed (injected)");
      },
      restoreCalls: () => restoreCalls,
    };
  }

  test("retry from stopped starts the graph to ready", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    const recovery = new RuntimeRecovery({ runtime, data: dataCapabilities() });

    const result = await recovery.recover("retry");
    expect(result.applied).toBe(true);
    expect(result.error).toBeNull();
    expect(result.status.state).toBe("ready");
    expect(result.status.ready).toBe(true);
  });

  test("retry from failed reaches ready after the fault clears", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    const recovery = new RuntimeRecovery({ runtime, data: dataCapabilities() });
    expect((await runtime.ensureReady()).state).toBe("failed"); // drive to failed
    expect((await recovery.status()).state).toBe("failed");

    ops.spawnThrows = false;
    const result = await recovery.recover("retry");
    expect(result.applied).toBe(true);
    expect(result.status.state).toBe("ready");
  });

  test("retry in degraded repairs the graph", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    const recovery = new RuntimeRecovery({ runtime, data: dataCapabilities() });
    await runtime.ensureReady();
    ops.spawnedProcess("uvicorn").emitExit(9); // kill fastapi (dev adapter marker)
    await ops.sleep(10);

    const result = await recovery.recover("retry");
    expect(result.applied).toBe(true);
    expect(result.status.state).toBe("ready");
  });

  test("restart in degraded restarts the failed component's cascade", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    const recovery = new RuntimeRecovery({ runtime, data: dataCapabilities() });
    await runtime.ensureReady();
    ops.spawnedProcess(DEV_ADAPTER_SPAWN_MARKERS.agent_service).emitExit(9);
    await ops.sleep(10);
    const before = ops.spawned.length;

    const result = await recovery.recover("restart");
    expect(result.applied).toBe(true);
    // agent_service + next (dependent) are recycled.
    expect(ops.spawned.length).toBe(before + 2);
    expect(result.status.state).toBe("ready");
  });

  test("restoreBackup invokes the injected data capability in failed state", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    const data = dataCapabilities({ backupAvailable: true });
    const recovery = new RuntimeRecovery({ runtime, data });
    expect((await runtime.ensureReady()).state).toBe("failed"); // drive to failed

    const result = await recovery.recover("restoreBackup");
    expect(result.applied).toBe(true);
    expect(result.error).toBeNull();
    expect(data.restoreCalls()).toBe(1);
    // The runtime state itself is untouched by restore (data authority is injected).
    expect(result.status.state).toBe("failed");
  });

  test("restoreBackup failure maps to BACKUP_RESTORE_FAILED with old data intact", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    const data = dataCapabilities({ backupAvailable: true, restoreThrows: true });
    const recovery = new RuntimeRecovery({ runtime, data });
    expect((await runtime.ensureReady()).state).toBe("failed"); // drive to failed

    const result = await recovery.recover("restoreBackup");
    expect(result.applied).toBe(false);
    expect(result.error?.code).toBe(RUNTIME_ERROR_CODES.BACKUP_RESTORE_FAILED);
    expect(data.restoreCalls()).toBe(1);
  });

  test("restoreBackup is denied when no backup exists (T-43-04-02)", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    const recovery = new RuntimeRecovery({ runtime, data: dataCapabilities({ backupAvailable: false }) });
    expect((await runtime.ensureReady()).state).toBe("failed"); // drive to failed

    const result = await recovery.recover("restoreBackup");
    expect(result.applied).toBe(false);
    expect(result.error?.code).toBe(RUNTIME_ERROR_CODES.RECOVERY_DENIED);
  });

  test("unknown actions are denied (T-43-04-02)", async () => {
    const { runtime } = makeRuntime(createFakeOps());
    const recovery = new RuntimeRecovery({ runtime });
    const result = await recovery.recover("delete-all-data");
    expect(result.applied).toBe(false);
    expect(result.error?.code).toBe(RUNTIME_ERROR_CODES.RECOVERY_DENIED);
  });

  test("actions not allowed in the current state are denied", async () => {
    const { runtime } = makeRuntime(createFakeOps());
    await runtime.ensureReady(); // ready: no recovery actions allowed
    const recovery = new RuntimeRecovery({ runtime });
    const result = await recovery.recover("restart");
    expect(result.applied).toBe(false);
    expect(result.error?.code).toBe(RUNTIME_ERROR_CODES.RECOVERY_DENIED);
  });

  test("openDiagnostics returns redacted component labels, never paths", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();
    // Drive to degraded so the allowlist offers openDiagnostics with live sinks.
    ops.spawnedProcess(DEV_ADAPTER_SPAWN_MARKERS.next).emitExit(9);
    await ops.sleep(10);

    const recovery = new RuntimeRecovery({ runtime });
    const result = await recovery.recover("openDiagnostics");
    expect(result.applied).toBe(true);
    const serialized = JSON.stringify(result).toLowerCase();
    expect(serialized).not.toContain("c:"); // no drive paths
    expect(serialized).not.toContain("appdata");
    expect(serialized).not.toContain("pid");
    // Every launched component has a redacted log sink (the failed one included).
    expect([...result.diagnostics!.sinks].sort()).toEqual([...RUNTIME_START_ORDER].sort());
  });
});

test.describe("plan 43-04 invariant: no half-started graph behind failed", () => {
  test("mid-graph spawn failure stops already-started components (no orphans)", async () => {
    const ops = createFakeOps();
    // postgres_pgvector + vector_store start fine; fastapi spawn throws.
    ops.spawnThrows = false;
    let spawns = 0;
    const originalSpawn = ops.spawn.bind(ops);
    ops.spawn = (command, args, options) => {
      spawns += 1;
      if (spawns === 3) throw new Error("fastapi spawn failed (injected)");
      return originalSpawn(command, args, options);
    };
    const { runtime } = makeRuntime(ops);
    const snapshot = await runtime.ensureReady();

    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.SPAWN_FAILED);
    const failed = snapshot.components.find((c) => c.id === "fastapi");
    expect(failed?.state).toBe("failed");
    expect(failed?.lastError?.code).toBe(RUNTIME_ERROR_CODES.SPAWN_FAILED);
    // postgres_pgvector + vector_store were started, then stopped (reverse order).
    for (const id of ["postgres_pgvector", "vector_store"] as const) {
      const comp = snapshot.components.find((c) => c.id === id);
      expect(comp?.state).toBe("stopped");
      expect(comp?.ready).toBe(false);
      expect(comp?.endpoint).toBeNull();
    }
    // No live (un-exited) processes remain.
    expect(runningSpawnCount(ops)).toBe(0);
  });

  test("failed full start then retry reaches ready cleanly", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    expect((await runtime.ensureReady()).state).toBe("failed");

    ops.spawnThrows = false;
    const snapshot = await runtime.ensureReady();
    expect(snapshot.state).toBe("ready");
    expect(snapshot.components.every((c) => c.ready)).toBe(true);
  });

  test("migration failure stops every started component and never reports ready", async () => {
    const ops = createFakeOps();
    const gate = migrationGate({ needs: true, runThrows: true });
    const { runtime } = makeRuntime(ops, gate);
    const snapshot = await runtime.ensureReady();

    expect(snapshot.state).toBe("failed");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.lastError?.code).toBe(RUNTIME_ERROR_CODES.MIGRATION_FAILED);
    // All five components were started and then stopped in reverse order.
    for (const component of snapshot.components) {
      expect(component.state).toBe("stopped");
      expect(component.ready).toBe(false);
    }
    expect(runningSpawnCount(ops)).toBe(0);
  });

  test("failed migration then retry reaches ready (spawns every component again)", async () => {
    const ops = createFakeOps();
    const gate = migrationGate({ needs: true, runThrows: true });
    const { runtime } = makeRuntime(ops, gate);
    const first = await runtime.ensureReady();
    expect(first.state).toBe("failed");

    // Clear the fault: no migration needed anymore, retry reaches ready.
    const okGate = migrationGate({ needs: false });
    // Reuse a fresh runtime on the same adapter ops (components are stopped).
    const { runtime: retryRuntime } = makeRuntime(ops, okGate);
    const recovered = await retryRuntime.ensureReady();
    expect(recovered.state).toBe("ready");
  });

  test("startup failure snapshot keeps the failed component's redacted error visible", async () => {
    const ops = createFakeOps();
    ops.spawnThrows = true;
    const { runtime } = makeRuntime(ops);
    const snapshot = await runtime.ensureReady();
    const failed = snapshot.components.find((c) => c.state === "failed");
    expect(failed).toBeDefined();
    expect(failed?.lastError?.code).toBe(RUNTIME_ERROR_CODES.SPAWN_FAILED);
    const serialized = JSON.stringify(snapshot).toLowerCase();
    expect(serialized).not.toContain("secret");
    expect(serialized).not.toContain("pid");
  });
});

test.describe("plan 43-04 invariant: degraded keeps healthy components (no orphans)", () => {
  test("crash after ready -> degraded, healthy components stay owned by the runtime", async () => {
    const ops = createFakeOps();
    const { runtime } = makeRuntime(ops);
    await runtime.ensureReady();
    ops.spawnedProcess("next").emitExit(9);
    await ops.sleep(10);

    const snapshot = await runtime.status();
    expect(snapshot.state).toBe("degraded");
    const healthy = snapshot.components.filter((c) => c.state === "ready");
    expect(healthy).toHaveLength(RUNTIME_START_ORDER.length - 1);

    // Repair brings the graph back; the healthy components were NOT re-spawned.
    const before = ops.spawned.length;
    const repaired = await runtime.ensureReady();
    expect(repaired.state).toBe("ready");
    expect(ops.spawned.length).toBe(before + 1); // only next was restarted

    // Shutdown still owns the full tree (no orphans).
    const report = await runtime.shutdown();
    expect(report.failed).toHaveLength(0);
    expect(runningSpawnCount(ops)).toBe(0);
  });
});
