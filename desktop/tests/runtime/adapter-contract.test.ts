/**
 * Shared adapter-contract suite (plan 43-01, Task 2/3).
 *
 * One suite, run against BOTH `DevelopmentProcessAdapter` and
 * `PackagedProcessAdapter`. The two adapters differ only in process
 * source/configuration and in the set of components they may launch; the
 * contract (stable error codes, idempotent start/stop, drain-then-kill
 * shutdown, unintentional-exit notification, no PID/executable leakage) is
 * asserted identically for both.
 *
 * Packaged adapter contract specifics (T-43-01-01): unapproved components fail
 * closed with UNSUPPORTED_IN_PACKAGED and NEVER reach the spawn seam — there is
 * no PATH/Docker fallback.
 *
 * Tests are pure unit tests (injected FakeOps, no real OS processes). Readiness
 * is contract-level: a component is ready only when its injected probe
 * succeeds; port-open alone is never sufficient (D-43-03, 43-02 does the
 * protocol-level probing).
 */
import { expect, test } from "@playwright/test";
import type { AdapterBudgets, ProcessAdapter, RuntimeComponent, RuntimeError } from "../../src/runtime/types";
import { RUNTIME_COMPONENTS } from "../../src/runtime/types";
import { DevelopmentProcessAdapter } from "../../src/runtime/development-process-adapter";
import { PackagedProcessAdapter } from "../../src/runtime/packaged-process-adapter";
import { createFakeOps, type FakeOps } from "./fake-process-ops";

const BUDGETS: Partial<AdapterBudgets> = {
  startTimeoutMs: 250,
  drainMs: 50,
  killMs: 50,
};

interface AdapterFixture {
  adapter: ProcessAdapter;
  ops: FakeOps;
}

function createAdapter(
  factory: (ops: FakeOps) => ProcessAdapter,
  options: { earlyExitCode?: number | null; spawnThrows?: boolean } = {},
): AdapterFixture {
  const ops = createFakeOps();
  if (options.earlyExitCode !== undefined) ops.earlyExitCode = options.earlyExitCode;
  if (options.spawnThrows !== undefined) ops.spawnThrows = options.spawnThrows;
  return { adapter: factory(ops), ops };
}

const DEVELOPMENT_FACTORY = (ops: FakeOps) =>
  new DevelopmentProcessAdapter(ops, BUDGETS, { repoRoot: "C:/fake-repo" });

const PACKAGED_FACTORY = (ops: FakeOps) =>
  new PackagedProcessAdapter(ops, BUDGETS, {
    electronExe: "C:/fake-resources/electron.exe",
    nextStandaloneServerJs: "C:/fake-resources/standalone/server.js",
  });

/** First launchable component for the adapter (used by success-path tests). */
function firstLaunchable(adapter: ProcessAdapter): RuntimeComponent {
  const first = adapter.launchable[0];
  if (first === undefined) throw new Error("adapter must launch at least one component");
  return first;
}

/**
 * A launchable component whose launch config uses an absolute executable path
 * (the only kind the existence check applies to — bare dev names resolve via
 * PATH and surface as SPAWN_FAILED instead).
 */
function absoluteCommandComponent(adapter: ProcessAdapter): RuntimeComponent {
  if (adapter.mode === "development") return "fastapi"; // resolved backend venv python.exe
  return "next"; // packaged electron.exe
}

/** First component the adapter must NOT launch (fail-closed candidate). */
function firstUnlaunchable(adapter: ProcessAdapter): RuntimeComponent | null {
  for (const id of RUNTIME_COMPONENTS) {
    if (!adapter.launchable.includes(id)) return id;
  }
  return null;
}

async function expectRejectedWithCode(
  promise: Promise<unknown>,
  code: RuntimeError["code"],
): Promise<void> {
  let error: unknown;
  try {
    await promise;
  } catch (cause) {
    error = cause;
  }
  expect(error, `expected rejection with code ${code}`).toBeDefined();
  expect((error as RuntimeError).code).toBe(code);
}

function runContractSuite(
  suiteName: string,
  factory: (ops: FakeOps) => ProcessAdapter,
): void {
  test.describe(`adapter contract: ${suiteName}`, () => {
    test("mode and launchable set are explicit", () => {
      const { adapter } = createAdapter(factory);
      expect(adapter.mode).toBe(suiteName === "development" ? "development" : "packaged");
      if (suiteName === "development") {
        expect([...adapter.launchable].sort()).toEqual([...RUNTIME_COMPONENTS].sort());
      } else {
        // Packaged mode is Phase 41 NO-GO bounded: only the approved bundled
        // Next standalone path is launchable; everything else fails closed.
        expect([...adapter.launchable]).toEqual(["next"]);
      }
    });

    test("describe() is a safe label and never leaks executable paths", () => {
      const { adapter } = createAdapter(factory);
      for (const id of RUNTIME_COMPONENTS) {
        const label = adapter.describe(id);
        expect(label.length).toBeGreaterThan(0);
        expect(label).not.toContain(":\\"); // no absolute Windows path
        expect(label).not.toContain("C:/");
      }
    });

    test("start success allocates a loopback endpoint and is idempotent", async () => {
      const { adapter, ops } = createAdapter(factory);
      const component = firstLaunchable(adapter);
      const started = await adapter.start(component);
      expect(started.component).toBe(component);
      expect(started.endpoint.host).toBe("127.0.0.1");
      expect(started.endpoint.port).toBeGreaterThan(0);
      expect(adapter.isRunning(component)).toBe(true);
      expect(adapter.endpoint(component)).toEqual(started.endpoint);

      const spawnCount = ops.spawned.length;
      const again = await adapter.start(component);
      expect(again.endpoint).toEqual(started.endpoint);
      expect(ops.spawned.length).toBe(spawnCount); // no second spawn
    });

    test("unlaunchable components fail closed and never reach the spawn seam", async () => {
      const { adapter, ops } = createAdapter(factory);
      const unlaunchable = firstUnlaunchable(adapter);
      if (unlaunchable === null) return; // development adapter launches everything
      await expectRejectedWithCode(adapter.start(unlaunchable), "UNSUPPORTED_IN_PACKAGED");
      expect(ops.spawned).toHaveLength(0); // never spawns, never falls back (T-43-01-01)
      expect(adapter.isRunning(unlaunchable)).toBe(false);
      await adapter.stop(unlaunchable); // stop is idempotent for never-started
    });

    test("missing absolute executable -> EXECUTABLE_NOT_FOUND", async () => {
      const { adapter, ops } = createAdapter(factory);
      ops.existsResult = false;
      const component = absoluteCommandComponent(adapter);
      await expectRejectedWithCode(adapter.start(component), "EXECUTABLE_NOT_FOUND");
      expect(ops.spawned).toHaveLength(0);
      expect(adapter.isRunning(component)).toBe(false);
    });

    test("spawn failure -> SPAWN_FAILED", async () => {
      const { adapter } = createAdapter(factory, { spawnThrows: true });
      const component = firstLaunchable(adapter);
      await expectRejectedWithCode(adapter.start(component), "SPAWN_FAILED");
      expect(adapter.isRunning(component)).toBe(false);
    });

    test("early exit before readiness -> EXIT_EARLY and process is forgotten", async () => {
      const { adapter } = createAdapter(factory, { earlyExitCode: 1 });
      const component = firstLaunchable(adapter);
      await expectRejectedWithCode(adapter.start(component), "EXIT_EARLY");
      expect(adapter.isRunning(component)).toBe(false);
      expect(adapter.endpoint(component)).toBeNull();
    });

    test("readiness timeout (port/probe never succeeds) -> START_TIMEOUT and cleanup", async () => {
      const { adapter, ops } = createAdapter(factory);
      ops.probeResult = false; // port open is NOT readiness
      const component = firstLaunchable(adapter);
      await expectRejectedWithCode(adapter.start(component), "START_TIMEOUT");
      expect(adapter.isRunning(component)).toBe(false);
      expect(adapter.endpoint(component)).toBeNull();
    });

    test("stop drains then force-kills the tree; repeated stop is idempotent", async () => {
      const { adapter, ops } = createAdapter(factory);
      const component = firstLaunchable(adapter);
      await adapter.start(component);
      expect(ops.spawned.length).toBe(1);

      await adapter.stop(component);
      expect(adapter.isRunning(component)).toBe(false);
      expect(adapter.endpoint(component)).toBeNull();

      await adapter.stop(component); // idempotent no-op
      expect(ops.killTreeCalls).toBe(0); // graceful drain succeeded, no force-kill
    });

    test("drain hang + kill failure -> STOP_KILL_FAILED and ownership retained", async () => {
      const { adapter, ops } = createAdapter(factory);
      const component = firstLaunchable(adapter);
      await adapter.start(component);
      ops.drainSucceeds = false;
      ops.killTreeSucceeds = false;
      await expectRejectedWithCode(adapter.stop(component), "STOP_KILL_FAILED");
      expect(adapter.isRunning(component)).toBe(true); // orphan possible; ownership retained
    });

    test("onExit fires only for unintentional exits after readiness", async () => {
      const { adapter, ops } = createAdapter(factory);
      const component = firstLaunchable(adapter);
      const exits: Array<number | null> = [];
      const unsubscribe = adapter.onExit(component, (code) => exits.push(code));

      await adapter.start(component);
      await adapter.stop(component); // intentional -> no notification
      await ops.sleep(10);
      expect(exits).toHaveLength(0);

      await adapter.start(component); // fresh owned process
      ops.spawnedProcess(componentMarker(adapter)).emitExit(9);
      await ops.sleep(10);
      expect(exits).toEqual([9]);
      expect(adapter.isRunning(component)).toBe(false);

      unsubscribe();
    });

    test("early exit before readiness does not fire onExit", async () => {
      const { adapter, ops } = createAdapter(factory, { earlyExitCode: 2 });
      const component = firstLaunchable(adapter);
      const exits: Array<number | null> = [];
      adapter.onExit(component, (code) => exits.push(code));
      await expectRejectedWithCode(adapter.start(component), "EXIT_EARLY");
      await ops.sleep(10);
      expect(exits).toHaveLength(0);
    });
  });
}

/** Marker used to look up the spawned FakeProcess for a component's launch. */
function componentMarker(adapter: ProcessAdapter): string {
  switch (adapter.mode) {
    case "development":
      return "next";
    case "packaged":
      return "server.js";
  }
}

runContractSuite("development", DEVELOPMENT_FACTORY);
runContractSuite("packaged", PACKAGED_FACTORY);
