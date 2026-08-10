/**
 * Five-component dependency graph supervisor (Phase 43, plan 43-02).
 *
 * `ProcessGraph` owns the graph analysis — topological order, cycle detection,
 * transitive dependents, dependency satisfaction — derived from the canonical
 * `COMPONENT_DEPENDENCIES` table in `types.ts`. A cycle or an unknown/missing
 * component is rejected before any process starts (fail-closed, D-43-03).
 *
 * `GraphSupervisor` is the concrete coordinator: it starts components strictly
 * in dependency order, gates every dependent start on the dependency chain
 * being present, applies the protocol-level readiness probes from
 * `readiness.ts` (T-43-02-03) with bounded deadlines, and never reports ready
 * while a mandatory component is failed/degraded. On any failure it stops the
 * already-started components (reverse order) and returns a typed failed result.
 */
import {
  COMPONENT_DEPENDENCIES,
  RUNTIME_COMPONENTS,
  RuntimeError,
  type AdapterBudgets,
  type ComponentEndpoint,
  type ProcessAdapter,
  type RuntimeComponent,
  type StartedProcess,
} from "./types";
import { DEFAULT_ADAPTER_BUDGETS } from "./base-process-adapter";
import {
  nodeReadinessTransport,
  waitForReadiness,
  type ReadinessTransport,
} from "./readiness";

export class GraphError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GraphError";
  }
}

export interface GraphValidation {
  ok: boolean;
  /** Dependency cycles, each as the ordered cycle path. */
  cycles: readonly (readonly RuntimeComponent[])[];
  /** Components referenced by dependencies but not part of the component set. */
  unknown: readonly RuntimeComponent[];
  /** Required components missing from the component set. */
  missing: readonly RuntimeComponent[];
}

/**
 * Dependency-graph analysis over the five runtime components. Pure — no
 * processes, no I/O — so it is exhaustively unit-testable.
 */
export class ProcessGraph {
  private readonly components: readonly RuntimeComponent[];
  private readonly dependencies: Readonly<
    Record<RuntimeComponent, readonly RuntimeComponent[]>
  >;
  private readonly dependents: Readonly<
    Record<RuntimeComponent, readonly RuntimeComponent[]>
  >;

  constructor(
    components: readonly RuntimeComponent[] = RUNTIME_COMPONENTS,
    dependencies: Readonly<
      Record<RuntimeComponent, readonly RuntimeComponent[]>
    > = COMPONENT_DEPENDENCIES,
  ) {
    this.components = components;
    this.dependencies = dependencies;
    const dependents = {} as Record<RuntimeComponent, RuntimeComponent[]>;
    for (const id of components) dependents[id] = [];
    for (const id of components) {
      for (const dep of dependencies[id] ?? []) {
        dependents[dep]?.push(id);
      }
    }
    this.dependents = dependents;
  }

  /** Every component in the graph, in dependency (topological) order. */
  get order(): readonly RuntimeComponent[] {
    return this.topologicalOrder();
  }

  dependenciesOf(id: RuntimeComponent): readonly RuntimeComponent[] {
    return this.dependencies[id] ?? [];
  }

  dependentsOf(id: RuntimeComponent): readonly RuntimeComponent[] {
    return this.dependents[id] ?? [];
  }

  /** Transitive dependents of `target` (the restart-cascade set). */
  affectedComponents(target: RuntimeComponent): readonly RuntimeComponent[] {
    const affected = new Set<RuntimeComponent>([target]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const id of this.components) {
        if (affected.has(id)) continue;
        if (this.dependenciesOf(id).some((dep) => affected.has(dep))) {
          affected.add(id);
          changed = true;
        }
      }
    }
    return this.order.filter((id) => affected.has(id));
  }

  /** Whether every dependency of `id` is present in the ready set. */
  isSatisfied(id: RuntimeComponent, ready: ReadonlySet<RuntimeComponent>): boolean {
    return this.dependenciesOf(id).every((dep) => ready.has(dep));
  }

  /**
   * Validates the graph: every component known, every dependency resolvable,
   * no cycles. Fail-closed — call before starting any process.
   */
  validate(): GraphValidation {
    const known = new Set<RuntimeComponent>(this.components);
    const unknown = new Set<RuntimeComponent>();
    for (const id of this.components) {
      for (const dep of this.dependencies[id] ?? []) {
        if (!known.has(dep)) unknown.add(dep);
      }
    }
    const missing = RUNTIME_COMPONENTS.filter((id) => !known.has(id));
    return { ok: unknown.size === 0 && missing.length === 0 && this.cycles().length === 0, cycles: this.cycles(), unknown: [...unknown], missing };
  }

  /** Detects dependency cycles via DFS (Kahn-style coloring). */
  cycles(): readonly (readonly RuntimeComponent[])[] {
    const WHITE = 0;
    const GRAY = 1;
    const BLACK = 2;
    const color = new Map<RuntimeComponent, number>();
    const cycles: RuntimeComponent[][] = [];

    const visit = (
      node: RuntimeComponent,
      stack: RuntimeComponent[],
    ): void => {
      color.set(node, GRAY);
      stack.push(node);
      for (const dep of this.dependenciesOf(node)) {
        const state = color.get(dep) ?? WHITE;
        if (state === GRAY) {
          // Found a cycle: slice the current stack from the first occurrence.
          const from = stack.indexOf(dep);
          if (from >= 0) cycles.push([...stack.slice(from), dep]);
        } else if (state === WHITE) {
          visit(dep, stack);
        }
      }
      stack.pop();
      color.set(node, BLACK);
    };

    for (const id of this.components) {
      if ((color.get(id) ?? WHITE) === WHITE) visit(id, []);
    }
    return cycles;
  }

  /** Topological order (Kahn's algorithm). Throws GraphError on a cycle. */
  topologicalOrder(): readonly RuntimeComponent[] {
    const cycles = this.cycles();
    if (cycles.length > 0) {
      throw new GraphError(
        `dependency cycle detected: ${cycles[0]?.join(" -> ") ?? "unknown"}`,
      );
    }
    const remaining = new Map<RuntimeComponent, number>();
    for (const id of this.components) {
      remaining.set(id, this.dependenciesOf(id).length);
    }
    const result: RuntimeComponent[] = [];
    const ready = this.components.filter((id) => remaining.get(id) === 0);
    while (ready.length > 0) {
      const id = ready.shift()!;
      result.push(id);
      for (const dep of this.dependentsOf(id)) {
        const count = (remaining.get(dep) ?? 0) - 1;
        remaining.set(dep, count);
        if (count === 0) ready.push(dep);
      }
    }
    return result;
  }
}

export interface GraphSupervisorOptions {
  adapter: ProcessAdapter;
  /** Strict protocol probes. Defaults to the node transport. */
  transport?: ReadinessTransport;
  /** Bounded budgets; the strict readiness wait uses `startTimeoutMs`. */
  budgets?: Partial<AdapterBudgets>;
  /** Optional injected graph (defaults to the canonical five-component graph). */
  graph?: ProcessGraph;
}

export interface GraphStartResult {
  ok: boolean;
  /** Components started and strict-ready (all of them when ok). */
  started: readonly RuntimeComponent[];
  /** The component that failed to become strict-ready, when !ok. */
  failed: RuntimeComponent | null;
  endpoints: ReadonlyMap<RuntimeComponent, ComponentEndpoint>;
}

/**
 * Concrete five-component supervisor. Sequentially starts components in
 * dependency order; a dependent only starts after every dependency is strict-
 * ready; never reports ready while a mandatory component is failed/degraded;
 * stops already-started components (reverse order) on failure.
 */
export class GraphSupervisor {
  private readonly adapter: ProcessAdapter;
  private readonly transport: ReadinessTransport;
  private readonly budgets: AdapterBudgets;
  private readonly graph: ProcessGraph;
  private readonly started = new Map<RuntimeComponent, StartedProcess>();
  private failure: RuntimeComponent | null = null;

  constructor(options: GraphSupervisorOptions) {
    this.adapter = options.adapter;
    this.transport = options.transport ?? nodeReadinessTransport();
    this.budgets = { ...DEFAULT_ADAPTER_BUDGETS, ...options.budgets };
    this.graph = options.graph ?? new ProcessGraph();
  }

  /** Components currently started and strict-ready (in dependency order). */
  get ready(): readonly RuntimeComponent[] {
    return this.graph.order.filter((id) => this.started.has(id));
  }

  isReady(): boolean {
    return this.failure === null && this.started.size === this.graph.order.length;
  }

  endpoint(id: RuntimeComponent): ComponentEndpoint | null {
    return this.started.get(id)?.endpoint ?? null;
  }

  /**
   * Starts the whole graph. Fails closed: on the first component that cannot
   * start or cannot reach strict readiness, the already-started components are
   * stopped in reverse dependency order and the result reports `ok: false`.
   */
  async start(): Promise<GraphStartResult> {
    const validation = this.graph.validate();
    if (!validation.ok) {
      throw new GraphError(
        `invalid runtime graph: ${JSON.stringify(validation)}`,
      );
    }
    this.failure = null;
    const endpoints = new Map<RuntimeComponent, ComponentEndpoint>();
    for (const id of this.graph.order) {
      const started = await this.startComponent(id, endpoints);
      if (started === null) {
        this.failure = id;
        const partial = new Map(this.started);
        await this.stop();
        return {
          ok: false,
          started: this.graph.order.filter((c) => partial.has(c)),
          failed: id,
          endpoints: new Map(
            this.graph.order.filter((c) => partial.has(c)).map((c) => [c, partial.get(c)!.endpoint]),
          ),
        };
      }
      this.started.set(id, started);
      endpoints.set(id, started.endpoint);
    }
    return { ok: true, started: this.ready, failed: null, endpoints };
  }

  /** Stops every started component in reverse dependency order. Idempotent. */
  async stop(): Promise<void> {
    for (const id of [...this.graph.order].reverse()) {
      if (!this.started.has(id)) continue;
      await this.adapter.stop(id);
      this.started.delete(id);
    }
  }

  /** Starts one component gated on its dependency chain, then strict-probes it. */
  private async startComponent(
    id: RuntimeComponent,
    endpoints: ReadonlyMap<RuntimeComponent, ComponentEndpoint>,
  ): Promise<StartedProcess | null> {
    // Dependency chain must be present before a dependent starts (D-43-03).
    const missing = this.graph.dependenciesOf(id).find((dep) => !endpoints.has(dep));
    if (missing !== undefined) return null;

    let started: StartedProcess;
    try {
      started = await this.adapter.start(id);
    } catch (cause) {
      if (cause instanceof RuntimeError) return null;
      throw cause;
    }
    const dependencies = new Map(
      this.graph
        .dependenciesOf(id)
        .filter((dep) => endpoints.has(dep))
        .map((dep) => [dep, endpoints.get(dep)!] as const),
    );
    const strictReady = await waitForReadiness(
      id,
      started.endpoint,
      dependencies,
      this.transport,
      { deadlineMs: this.budgets.startTimeoutMs },
      () => this.adapter.isRunning(id),
    );
    if (!strictReady) {
      try {
        await this.adapter.stop(id);
      } catch {
        // The component is failed either way; surface the readiness failure.
      }
      return null;
    }
    return started;
  }
}
