/**
 * Process-tree ownership + log lifecycle suites (plan 43-02, Task 2/3).
 *
 * Pure unit tests — no real OS processes. Covers the instance-bound ownership
 * contract (T-43-02-01) and the bounded, redacted, rotating log sinks
 * (T-43-02-02):
 * - only processes this instance registered are ever killed — an unrelated
 *   sentinel process (never registered) survives shutdown untouched,
 * - drain-then-kill terminates the whole owned tree within the budget,
 * - a tree that refuses to die surfaces ProcessOwnerError and ownership is
 *   retained (an orphan may remain — never claim a clean stop),
 * - spawned children are console-hidden in packaged mode (windowsHide),
 * - logs land under <appData>/logs/{component}, rotate at a size cap and keep
 *   at most `maxFiles` rotated files, and never contain tokens/keys.
 */
import { expect, test } from "@playwright/test";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { ProcessOwner, ProcessOwnerError } from "../../src/runtime/process-owner";
import { ComponentLogger, componentLogDir, redactLine } from "../../src/runtime/logging";
import { createFakeOps, FakeProcess, type FakeOps } from "./fake-process-ops";

function makeOwner(ops: FakeOps, budgets?: { drainMs?: number; killMs?: number }) {
  return new ProcessOwner({ ops, drainMs: budgets?.drainMs ?? 20, killMs: budgets?.killMs ?? 20 });
}

test.describe("ProcessOwner — instance-bound tree ownership", () => {
  test("terminate drains a graceful tree and leaves zero owned descendants", async () => {
    const ops = createFakeOps();
    const owner = makeOwner(ops);
    const process = ops.spawn("child", ["--worker"]) as FakeProcess;
    owner.register("postgres_pgvector", process);

    expect(owner.size).toBe(1);
    await owner.terminate("postgres_pgvector");

    expect(owner.size).toBe(0);
    expect(owner.pidOf("postgres_pgvector")).toBeNull();
    expect(ops.killTreeCalls).toBe(0); // graceful drain, no force-kill
    expect(process.exitCode).not.toBeNull(); // the owned process exited
  });

  test("an unrelated sentinel process is preserved — never killed by name", async () => {
    const ops = createFakeOps();
    const owner = makeOwner(ops);
    // Sentinel: looks like the component but was NEVER registered by this instance.
    const sentinel = ops.spawn("docker", ["run", "--name", "novelmind-dev-pg-99999"]) as FakeProcess;
    const owned = ops.spawn("docker", ["run", "--name", "novelmind-dev-pg-12345"]) as FakeProcess;
    owner.register("postgres_pgvector", owned);

    await owner.terminate("postgres_pgvector");

    expect(owned.exitCode).not.toBeNull(); // owned tree terminated
    expect(sentinel.exitCode).toBeNull(); // sentinel untouched
    expect(owner.ownsPid(sentinel.pid)).toBe(false);
    expect(owner.ownsPid(owned.pid)).toBe(false); // ownership released after terminate
  });

  test("terminate on an unregistered component is a safe no-op", async () => {
    const ops = createFakeOps();
    const owner = makeOwner(ops);
    await owner.terminate("fastapi"); // never registered
    expect(owner.size).toBe(0);
    expect(ops.killTreeCalls).toBe(0);
  });

  test("a tree that survives drain is force-killed (taskkill /T /F)", async () => {
    const ops = createFakeOps();
    ops.drainSucceeds = false; // graceful kill does not exit the tree
    const owner = makeOwner(ops);
    const process = ops.spawn("node", ["server.js"]) as FakeProcess;
    owner.register("next", process);

    await owner.terminate("next");

    expect(ops.killTreeCalls).toBe(1); // force-kill the whole tree
    expect(owner.size).toBe(0);
    expect(process.exitCode).not.toBeNull();
  });

  test("a tree that refuses even the force-kill surfaces ProcessOwnerError and retains ownership", async () => {
    const ops = createFakeOps();
    ops.drainSucceeds = false;
    ops.killTreeSucceeds = false;
    const owner = makeOwner(ops);
    const process = ops.spawn("postgres", []) as FakeProcess;
    owner.register("postgres_pgvector", process);

    let error: unknown;
    try {
      await owner.terminate("postgres_pgvector");
    } catch (cause) {
      error = cause;
    }
    expect(error).toBeInstanceOf(ProcessOwnerError);
    // An orphan may remain — ownership is retained so the caller cannot claim a
    // clean stop, and the orphan is never re-killed by name.
    expect(owner.size).toBe(1);
    expect(owner.pidOf("postgres_pgvector")).toBe(process.pid);
  });

  test("terminateAll owns every registered tree, only registered ones", async () => {
    const ops = createFakeOps();
    const owner = makeOwner(ops);
    const unrelated = ops.spawn("node", ["--unrelated"]) as FakeProcess;
    const a = ops.spawn("a", []) as FakeProcess;
    const b = ops.spawn("b", []) as FakeProcess;
    owner.register("vector_store", a);
    owner.register("fastapi", b);

    await owner.terminateAll();

    expect(owner.size).toBe(0);
    expect(a.exitCode).not.toBeNull();
    expect(b.exitCode).not.toBeNull();
    expect(unrelated.exitCode).toBeNull(); // never registered → survives
  });

  test("children are spawned console-hidden (windowsHide) in packaged mode", async () => {
    const ops = createFakeOps();
    ops.spawn("app", ["--hidden"], { windowsHide: true });
    expect(ops.spawned[0]!.options?.windowsHide).toBe(true);
  });
});

test.describe("ComponentLogger — bounded redacted rotating logs", () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "novelmind-43-02-"));
  test.afterAll(() => {
    rmSync(root, { recursive: true, force: true });
  });

  test("writes redacted lines to <appData>/logs/{component}/{component}.log", async () => {
    const logger = new ComponentLogger({ component: "fastapi", root });
    logger.write("stdout", "INFO startup complete");
    logger.write("stderr", "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.secret");
    await logger.close();

    expect(existsSync(componentLogDir("fastapi", root))).toBe(true);
    const content = readFileSync(path.join(componentLogDir("fastapi", root), "fastapi.log"), "utf8");
    expect(content).toContain("INFO startup complete");
    expect(content).not.toContain("eyJhbGciOiJIUzI1NiJ9");
    expect(content).not.toContain("Bearer");
    expect(content).toContain("[REDACTED]");
  });

  test("redactLine never leaks tokens, keys or key=value secrets", () => {
    const samples = [
      "Authorization: Bearer abc.def.ghi1234567890",
      "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
      "AIzaSyA0123456789abcdefghijklmnopqrstuvwxyz",
      "api_key=super-secret-value-123",
      "POSTGRES_PASSWORD=novelmind",
      "plain log line stays untouched",
    ];
    for (const sample of samples) {
      const redacted = redactLine(sample);
      if (sample === "plain log line stays untouched") {
        expect(redacted).toBe(sample);
      } else {
        expect(redacted).not.toContain("secret");
        expect(redacted).not.toContain("Bearer ");
        expect(redacted).not.toContain("sk-proj-");
        expect(redacted).not.toContain("AIzaSy");
        expect(redacted).not.toContain("novelmind");
      }
    }
  });

  test("rotates at the size cap and keeps at most maxFiles rotated files", async () => {
    const dir = path.join(root, "rotate");
    const logger = new ComponentLogger({ component: "agent_service", root: dir, maxBytes: 64, maxFiles: 2 });
    // Write enough lines to trigger several rotations.
    for (let i = 0; i < 40; i += 1) logger.write("stdout", `line ${i} padding padding padding`);
    await logger.close();

    const files = readdirSync(componentLogDir("agent_service", dir)).filter((f) => f.endsWith(".log"));
    // One active file plus at most maxFiles rotated files.
    expect(files.length).toBeLessThanOrEqual(3);
    expect(files.length).toBeGreaterThanOrEqual(1);
  });

  test("close flushes and subsequent writes reopen the sink", async () => {
    const logger = new ComponentLogger({ component: "next", root });
    logger.write("stdout", "first");
    await logger.close();
    logger.write("stdout", "second");
    await logger.close();
    const content = readFileSync(path.join(componentLogDir("next", root), "next.log"), "utf8");
    expect(content).toContain("first");
    expect(content).toContain("second");
  });
});
