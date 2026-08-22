/**
 * Instance-bound process-tree ownership (Phase 43, plan 43-02, T-43-02-01).
 *
 * The owner records ONLY the processes this runtime instance spawned — a PID
 * is a member of the owned tree by registration, never by executable name.
 * `terminate` performs the bounded drain-then-kill sequence for a component's
 * whole tree (graceful `kill()` → `taskkill /T /F`), and refuses to touch
 * anything that was never registered, so unrelated matching-name processes and
 * user processes always survive shutdown.
 *
 * Registered children are additionally bound to a `RuntimeComponent`, so a
 * targeted restart can recycle exactly one component's tree (D-43-07).
 */
import type { ProcessOperations, SpawnedProcess } from "./process-operations";
import { nodeProcessOperations } from "./process-operations";
import type { RuntimeComponent } from "./types";

export class ProcessOwnerError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProcessOwnerError";
  }
}

export interface ProcessOwnerOptions {
  /** Injected process operations (fakes in tests, nodeProcessOperations live). */
  ops?: ProcessOperations;
  /** Graceful drain window before the tree is force-killed. */
  drainMs?: number;
  /** Max time to wait for the tree to die after force-kill. */
  killMs?: number;
}

/**
 * Owns the process trees of one runtime instance. Instance-bound: nothing is
 * ever killed by name, only by a PID registered through `register`.
 */
export class ProcessOwner {
  private readonly ops: ProcessOperations;
  private readonly drainMs: number;
  private readonly killMs: number;
  private readonly owned = new Map<RuntimeComponent, SpawnedProcess>();

  constructor(options: ProcessOwnerOptions = {}) {
    this.ops = options.ops ?? nodeProcessOperations();
    this.drainMs = options.drainMs ?? 3_000;
    this.killMs = options.killMs ?? 3_000;
  }

  /** Registers the spawned process as owned by this instance for `component`. */
  register(component: RuntimeComponent, process: SpawnedProcess): void {
    this.owned.set(component, process);
  }

  /** Drops ownership of a component's tree. Idempotent. */
  unregister(component: RuntimeComponent): void {
    this.owned.delete(component);
  }

  has(component: RuntimeComponent): boolean {
    return this.owned.has(component);
  }

  pidOf(component: RuntimeComponent): number | null {
    return this.owned.get(component)?.pid ?? null;
  }

  /** Whether this instance owns the exact PID (never a name match). */
  ownsPid(pid: number): boolean {
    for (const process of this.owned.values()) {
      if (process.pid === pid) return true;
    }
    return false;
  }

  get size(): number {
    return this.owned.size;
  }

  /**
   * Drains then force-kills a component's owned process tree within bounded
   * timeouts. On success the component is unregistered. On failure ownership is
   * RETAINED (an orphan may remain — the caller must not claim a clean stop).
   */
  async terminate(component: RuntimeComponent): Promise<void> {
    const process = this.owned.get(component);
    if (process === undefined) return; // idempotent for never-owned/stopped
    process.kill();
    const drained = await this.ops.waitForExit(process, this.drainMs);
    if (drained !== null) {
      this.owned.delete(component);
      return;
    }
    await this.ops.killTree(process.pid);
    const killed = await this.ops.waitForExit(process, this.killMs);
    if (killed === null) {
      throw new ProcessOwnerError(
        `could not terminate ${component} process tree within the kill budget`,
      );
    }
    this.owned.delete(component);
  }

  /** Terminates every owned tree. Continues after individual failures. */
  async terminateAll(): Promise<void> {
    for (const component of [...this.owned.keys()]) {
      await this.terminate(component);
    }
  }
}
