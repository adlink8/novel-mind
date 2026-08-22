/**
 * Shared process-adapter machinery (Phase 43, plan 43-01).
 *
 * `BaseProcessAdapter` owns the process map, endpoint map, spawn/readiness flow,
 * drain-then-kill shutdown and unintentional-exit notification. The two concrete
 * adapters differ ONLY in their launch configuration and the set of components
 * they are allowed to launch (T-43-01-01):
 *
 * - `DevelopmentProcessAdapter` may launch every component via existing local
 *   dev entrypoints.
 * - `PackagedProcessAdapter` launches only Phase 41 approved bundled paths and
 *   fails closed for everything else.
 *
 * Process/PID/executable internals stay private to this layer; the runtime only
 * ever sees typed snapshots and endpoints.
 */
import path from "node:path";
import {
  LOOPBACK_HOST,
  RUNTIME_ERROR_CODES,
  RuntimeError,
  type AdapterBudgets,
  type ComponentEndpoint,
  type ComponentLaunch,
  type ProcessAdapter,
  type ProbeConfig,
  type RuntimeComponent,
  type StartedProcess,
} from "./types";
import type { ProcessOperations, SpawnedProcess } from "./process-operations";

export const DEFAULT_ADAPTER_BUDGETS: AdapterBudgets = {
  startTimeoutMs: 60_000,
  drainMs: 3_000,
  killMs: 3_000,
};

const PROBE_INTERVAL_MS = 200;
const PROBE_TIMEOUT_MS = 2_000;

interface OwnedProcess {
  child: SpawnedProcess;
  /** start() resolved successfully; exits after this are "unintentional". */
  startSettled: boolean;
}

export abstract class BaseProcessAdapter implements ProcessAdapter {
  abstract readonly mode: "development" | "packaged";
  abstract readonly launchable: readonly RuntimeComponent[];

  protected readonly ops: ProcessOperations;
  protected readonly budgets: AdapterBudgets;
  private readonly owned = new Map<RuntimeComponent, OwnedProcess>();
  private readonly endpoints = new Map<RuntimeComponent, ComponentEndpoint>();
  private readonly exitListeners = new Map<
    RuntimeComponent,
    Set<(code: number | null) => void>
  >();
  private readonly intentionalStops = new Set<RuntimeComponent>();

  constructor(ops: ProcessOperations, budgets?: Partial<AdapterBudgets>) {
    this.ops = ops;
    this.budgets = { ...DEFAULT_ADAPTER_BUDGETS, ...budgets };
  }

  protected abstract launchConfig(component: RuntimeComponent): ComponentLaunch;

  async start(component: RuntimeComponent): Promise<StartedProcess> {
    if (!this.launchable.includes(component)) {
      // Fail closed: never falls back to PATH executables or Docker (T-43-01-01).
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.UNSUPPORTED_IN_PACKAGED,
        `no approved launch path for ${component} in ${this.mode} mode`,
        component,
      );
    }
    const existing = this.owned.get(component);
    if (existing !== undefined) {
      const endpoint = this.endpoints.get(component);
      if (endpoint === undefined) {
        throw new RuntimeError(
          RUNTIME_ERROR_CODES.INTERNAL,
          `missing endpoint for ${component}`,
          component,
        );
      }
      return { component, endpoint }; // idempotent while running
    }

    const config = this.launchConfig(component);
    // Only absolute executable paths are existence-checked; bare command names
    // (dev mode) are resolved by the OS via PATH at spawn time and surface as
    // SPAWN_FAILED when absent.
    if (path.isAbsolute(config.command) && !this.ops.exists(config.command)) {
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.EXECUTABLE_NOT_FOUND,
        `executable not found for ${component}`,
        component,
      );
    }

    const port = await this.ops.allocateLoopbackPort();
    const { command, args, cwd, env } = this.applyPort(config, port);
    let child: SpawnedProcess;
    try {
      child = this.ops.spawn(command, args, { cwd, env, windowsHide: true });
    } catch {
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.SPAWN_FAILED,
        `failed to spawn ${component}`,
        component,
      );
    }
    this.owned.set(component, { child, startSettled: false });
    this.endpoints.set(component, { host: LOOPBACK_HOST, port });
    child.onExit((code) => this.handleExit(component, code));

    const outcome = await this.waitReady(component, config.probe);
    if (outcome === "exited") {
      const exitCode = child.exitCode;
      this.forget(component);
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.EXIT_EARLY,
        `component ${component} exited before readiness (code ${exitCode ?? "unknown"})`,
        component,
      );
    }
    if (outcome === "timeout") {
      await this.forceStopTree(component);
      this.forget(component);
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.START_TIMEOUT,
        `component ${component} did not become ready within ${this.budgets.startTimeoutMs}ms`,
        component,
      );
    }

    const owned = this.owned.get(component);
    if (owned !== undefined) owned.startSettled = true;
    return { component, endpoint: this.endpoints.get(component)! };
  }

  async stop(component: RuntimeComponent): Promise<void> {
    const owned = this.owned.get(component);
    if (owned === undefined) return; // idempotent
    this.intentionalStops.add(component);

    // 1. Graceful drain window.
    owned.child.kill();
    const drained = await this.ops.waitForExit(owned.child, this.budgets.drainMs);
    if (drained !== null) {
      this.forget(component);
      return;
    }

    // 2. Force-kill the whole Windows process tree (taskkill /T /F).
    await this.ops.killTree(owned.child.pid);
    const killed = await this.ops.waitForExit(owned.child, this.budgets.killMs);
    if (killed === null) {
      this.intentionalStops.delete(component);
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.STOP_KILL_FAILED,
        `could not terminate ${component} process tree`,
        component,
      );
    }
    this.forget(component);
  }

  endpoint(component: RuntimeComponent): ComponentEndpoint | null {
    return this.endpoints.get(component) ?? null;
  }

  isRunning(component: RuntimeComponent): boolean {
    return this.owned.has(component);
  }

  describe(component: RuntimeComponent): string {
    return `${this.mode} ${component}`;
  }

  onExit(component: RuntimeComponent, listener: (code: number | null) => void): () => void {
    let listeners = this.exitListeners.get(component);
    if (listeners === undefined) {
      listeners = new Set();
      this.exitListeners.set(component, listeners);
    }
    listeners.add(listener);
    return () => {
      const current = this.exitListeners.get(component);
      current?.delete(listener);
    };
  }

  private handleExit(component: RuntimeComponent, code: number | null): void {
    if (this.intentionalStops.has(component)) return;
    const owned = this.owned.get(component);
    if (owned === undefined || !owned.startSettled) return;
    this.forget(component);
    const listeners = this.exitListeners.get(component);
    if (listeners === undefined) return;
    for (const listener of [...listeners]) listener(code);
  }

  private forget(component: RuntimeComponent): void {
    this.owned.delete(component);
    this.endpoints.delete(component);
    this.intentionalStops.delete(component);
  }

  private async waitReady(
    component: RuntimeComponent,
    probe: ProbeConfig,
  ): Promise<"ready" | "exited" | "timeout"> {
    const deadline = Date.now() + this.budgets.startTimeoutMs;
    while (true) {
      const owned = this.owned.get(component);
      if (owned === undefined || owned.child.exitCode !== null) return "exited";
      if (await this.probe(component, probe)) return "ready";
      if (Date.now() >= deadline) return "timeout";
      await this.ops.sleep(PROBE_INTERVAL_MS);
    }
  }

  private async probe(component: RuntimeComponent, probe: ProbeConfig): Promise<boolean> {
    const endpoint = this.endpoints.get(component);
    if (endpoint === undefined) return false;
    try {
      if (probe.transport === "http") {
        return await this.ops.probeHttp(
          endpoint.host,
          endpoint.port,
          probe.path ?? "/",
          PROBE_TIMEOUT_MS,
        );
      }
      return await this.ops.probeTcp(endpoint.host, endpoint.port, PROBE_TIMEOUT_MS);
    } catch {
      return false;
    }
  }

  private async forceStopTree(component: RuntimeComponent): Promise<void> {
    const owned = this.owned.get(component);
    if (owned === undefined) return;
    await this.ops.killTree(owned.child.pid);
    await this.ops.waitForExit(owned.child, this.budgets.killMs);
  }

  private applyPort(
    config: ComponentLaunch,
    port: number,
  ): { command: string; args: readonly string[]; cwd: string | undefined; env: NodeJS.ProcessEnv | undefined } {
    const args = [...config.args];
    if (config.portVia.kind === "env") {
      const env = { ...config.env, [config.portVia.name]: String(port) };
      return { command: config.command, args, cwd: config.cwd, env };
    }
    const value =
      config.portVia.valueSuffix === undefined
        ? String(port)
        : `${port}${config.portVia.valueSuffix}`;
    args.push(config.portVia.flag, value);
    return { command: config.command, args, cwd: config.cwd, env: config.env };
  }
}
