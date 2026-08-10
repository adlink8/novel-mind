/**
 * DesktopRuntime — the deep lifecycle module (D-43-01, D-43-04).
 *
 * Public surface is exactly four methods: `ensureReady`, `status`, `restart`,
 * `shutdown`. Everything else — dependency-ordered orchestration, state machine,
 * crash-to-degraded handling, targeted restart cascades, shutdown draining — is
 * internal. The runtime is never a domain or persistence authority: migration is
 * injected (never owned), and this module imports no backend domain code (the
 * plan 43-01 Task 3 static scan enforces that).
 *
 * State machine (explicit table, no ambiguous edges):
 *
 *   stopped  ──▶ starting ──▶ migrating ──▶ ready
 *                │  │  └──────────┴────────▶ degraded
 *                │  └─────────────▶ failed   (component/migration failure)
 *                │        └──────▶ stopping ──▶ stopped | failed
 *   ready ──▶ degraded ──▶ ready | failed | stopping
 *   degraded ──▶ stopping
 *   failed ──▶ starting | stopping
 *
 * Invariant: `ready` is only entered via `transitionTo("ready")`, which asserts
 * every component is ready — "ready with a failed dependency" is unrepresentable.
 */
import {
  COMPONENT_DEPENDENCIES,
  isRuntimeComponent,
  noMigrationGate,
  RUNTIME_ERROR_CODES,
  RUNTIME_START_ORDER,
  RuntimeError,
  type ComponentSnapshot,
  type MigrationGate,
  type ProcessAdapter,
  type RedactedError,
  type RuntimeComponent,
  type RuntimeSnapshot,
  type RuntimeState,
  type ShutdownReport,
} from "./types";

/** Legal runtime-state transitions. Explicit table, no self-loops, no ambiguity. */
export const CAN_TRANSITION: Readonly<Record<RuntimeState, readonly RuntimeState[]>> = {
  stopped: ["starting"],
  starting: ["migrating", "ready", "failed", "stopping"],
  migrating: ["ready", "failed", "stopping"],
  ready: ["degraded", "stopping"],
  degraded: ["ready", "failed", "stopping"],
  failed: ["starting", "stopping"],
  // A shutdown that cannot terminate its owned tree ends `failed` (honest):
  // a live orphan may remain, so the runtime is NOT cleanly stopped.
  stopping: ["stopped", "failed"],
};

export function canTransition(from: RuntimeState, to: RuntimeState): boolean {
  return CAN_TRANSITION[from].includes(to);
}

export interface DesktopRuntimeOptions {
  adapter: ProcessAdapter;
  /** Optional migration gate. Defaults to "no migration needed". */
  migration?: MigrationGate;
}

function stoppedSnapshot(id: RuntimeComponent): ComponentSnapshot {
  return { id, state: "stopped", ready: false, endpoint: null, lastError: null };
}

function toRedacted(cause: unknown, fallback: RedactedError["code"] = RUNTIME_ERROR_CODES.INTERNAL): RedactedError {
  if (cause instanceof RuntimeError) return cause.redacted();
  // Never leak raw messages from unknown sources: fixed redacted message.
  return { code: fallback, message: "unexpected runtime error" };
}

export class DesktopRuntime {
  private readonly adapter: ProcessAdapter;
  private readonly migration: MigrationGate;
  private state: RuntimeState = "stopped";
  private readonly components: Record<RuntimeComponent, ComponentSnapshot>;
  private startedAt: string | null = null;
  private lastError: RedactedError | null = null;
  private flowActive = false;

  constructor(options: DesktopRuntimeOptions) {
    this.adapter = options.adapter;
    this.migration = options.migration ?? noMigrationGate();
    this.components = Object.fromEntries(
      RUNTIME_START_ORDER.map((id) => [id, stoppedSnapshot(id)]),
    ) as Record<RuntimeComponent, ComponentSnapshot>;
    for (const id of RUNTIME_START_ORDER) {
      this.adapter.onExit(id, (code) => this.handleUnexpectedExit(id, code));
    }
  }

  async ensureReady(): Promise<RuntimeSnapshot> {
    if (this.flowActive) return this.status();
    switch (this.state) {
      case "ready":
        return this.status();
      case "starting":
      case "migrating":
        return this.status(); // in-progress (only reachable under flowActive)
      case "stopping":
        throw new RuntimeError(RUNTIME_ERROR_CODES.BUSY, "runtime is stopping; cannot start");
      case "stopped":
      case "failed":
        return this.startGraph();
      case "degraded":
        return this.repairGraph();
    }
  }

  status(): Promise<RuntimeSnapshot> {
    return Promise.resolve(this.snapshot());
  }

  async restart(target?: RuntimeComponent): Promise<RuntimeSnapshot> {
    if (target !== undefined && !isRuntimeComponent(target)) {
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.COMPONENT_UNKNOWN,
        `unknown component: ${String(target)}`,
      );
    }
    if (this.flowActive) return this.status(); // mid-startup; report current state
    if (this.state === "stopping") {
      throw new RuntimeError(RUNTIME_ERROR_CODES.BUSY, "runtime is stopping");
    }

    if (target === undefined) {
      // Whole-graph recycle.
      if (this.state === "stopped" || this.state === "failed") return this.ensureReady();
      await this.stopAll();
      return this.startGraph();
    }

    // Targeted restart: recycle the target and every transitive dependent
    // (D-43-07 — unaffected services are preserved).
    if (this.state === "stopped" || this.state === "failed") return this.ensureReady();
    const affected = this.affectedComponents(target);
    this.transitionTo("degraded"); // no longer fully ready while the target cycles

    for (const id of [...affected].reverse()) {
      const comp = this.components[id];
      comp.state = "stopping";
      try {
        await this.adapter.stop(id);
        comp.state = "stopped";
        comp.ready = false;
        comp.endpoint = null;
        comp.lastError = null;
      } catch (cause) {
        const redacted = toRedacted(cause);
        comp.state = "failed";
        comp.lastError = redacted;
        this.lastError = redacted;
        this.transitionTo("failed");
        return this.snapshot();
      }
    }

    const restarted = await this.startComponents(affected);
    if (!restarted) {
      this.transitionTo("failed");
      return this.snapshot();
    }
    if (this.allComponentsReady()) this.transitionTo("ready");
    return this.snapshot();
  }

  async shutdown(): Promise<ShutdownReport> {
    if (this.state === "stopped") return { stopped: [], failed: [] }; // idempotent
    if (this.state === "stopping") {
      throw new RuntimeError(RUNTIME_ERROR_CODES.BUSY, "runtime is already stopping");
    }
    return this.stopAll();
  }

  private async startGraph(): Promise<RuntimeSnapshot> {
    this.flowActive = true;
    try {
      this.transitionTo("starting");
      const started = await this.startComponents(RUNTIME_START_ORDER);
      if (!started) {
        this.transitionTo("failed");
        return this.snapshot();
      }

      if (await this.migration.needsMigration()) {
        this.transitionTo("migrating");
        try {
          await this.migration.run();
        } catch (cause) {
          const redacted = toRedacted(cause, RUNTIME_ERROR_CODES.MIGRATION_FAILED);
          this.lastError = redacted;
          this.transitionTo("failed");
          return this.snapshot();
        }
      }

      if (this.allComponentsReady()) {
        this.startedAt = new Date().toISOString();
        this.transitionTo("ready");
      } else {
        this.transitionTo("degraded");
      }
      return this.snapshot();
    } finally {
      this.flowActive = false;
    }
  }

  private async repairGraph(): Promise<RuntimeSnapshot> {
    this.flowActive = true;
    try {
      const notReady = RUNTIME_START_ORDER.filter(
        (id) => this.components[id].state !== "ready",
      );
      const repaired = await this.startComponents(notReady);
      if (!repaired) {
        this.transitionTo("failed");
        return this.snapshot();
      }
      if (this.allComponentsReady()) this.transitionTo("ready");
      return this.snapshot();
    } finally {
      this.flowActive = false;
    }
  }

  private async startComponents(order: readonly RuntimeComponent[]): Promise<boolean> {
    for (const id of order) {
      const comp = this.components[id];
      if (comp.state === "ready") continue; // already running (idempotent recovery)
      comp.state = "starting";
      comp.lastError = null;
      try {
        const started = await this.adapter.start(id);
        comp.state = "ready";
        comp.ready = true;
        comp.endpoint = started.endpoint;
        comp.lastError = null;
      } catch (cause) {
        const redacted = toRedacted(cause);
        comp.state = "failed";
        comp.ready = false;
        comp.endpoint = null;
        comp.lastError = redacted;
        this.lastError = redacted;
        return false;
      }
    }
    return true;
  }

  private async stopAll(): Promise<ShutdownReport> {
    this.transitionTo("stopping");
    const stopped: RuntimeComponent[] = [];
    const failed: { component: RuntimeComponent; error: RedactedError }[] = [];
    for (const id of [...RUNTIME_START_ORDER].reverse()) {
      const comp = this.components[id];
      comp.state = "stopping";
      try {
        await this.adapter.stop(id);
        comp.state = "stopped";
        comp.ready = false;
        comp.endpoint = null;
        comp.lastError = null;
        stopped.push(id);
      } catch (cause) {
        const redacted = toRedacted(cause);
        comp.state = "failed";
        comp.lastError = redacted;
        failed.push({ component: id, error: redacted });
      }
    }
    if (failed.length > 0) {
      // A process tree could not be terminated — an orphan may remain.
      // Report honestly instead of claiming a clean stop.
      this.lastError = failed[0]?.error ?? null;
      this.transitionTo("failed");
    } else {
      this.transitionTo("stopped");
    }
    return { stopped, failed };
  }

  private transitionTo(next: RuntimeState): void {
    if (next === this.state) return;
    if (!canTransition(this.state, next)) {
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.ILLEGAL_TRANSITION,
        `illegal runtime transition ${this.state} -> ${next}`,
      );
    }
    if (next === "ready" && !this.allComponentsReady()) {
      throw new RuntimeError(
        RUNTIME_ERROR_CODES.READY_INVARIANT_VIOLATION,
        "cannot enter ready while a component is not ready",
      );
    }
    this.state = next;
  }

  private allComponentsReady(): boolean {
    return RUNTIME_START_ORDER.every((id) => this.components[id].ready);
  }

  private handleUnexpectedExit(component: RuntimeComponent, code: number | null): void {
    if (this.state === "stopping") return;
    const comp = this.components[component];
    if (comp.state !== "ready") return;
    comp.state = "failed";
    comp.ready = false;
    comp.endpoint = null;
    comp.lastError = {
      code: RUNTIME_ERROR_CODES.EXIT_EARLY,
      message: `component ${component} exited unexpectedly (code ${code ?? "unknown"})`,
    };
    this.lastError = comp.lastError;
    if (this.state === "ready") {
      this.transitionTo("degraded"); // crash after ready -> degraded (D-43-08)
    }
  }

  private affectedComponents(target: RuntimeComponent): readonly RuntimeComponent[] {
    const affected = new Set<RuntimeComponent>([target]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const id of RUNTIME_START_ORDER) {
        if (affected.has(id)) continue;
        if (COMPONENT_DEPENDENCIES[id].some((dep) => affected.has(dep))) {
          affected.add(id);
          changed = true;
        }
      }
    }
    return RUNTIME_START_ORDER.filter((id) => affected.has(id));
  }

  private snapshot(): RuntimeSnapshot {
    return {
      version: 1,
      state: this.state,
      ready: this.state === "ready",
      components: RUNTIME_START_ORDER.map((id) => ({ ...this.components[id] })),
      startedAt: this.startedAt,
      lastError: this.lastError,
    };
  }
}
