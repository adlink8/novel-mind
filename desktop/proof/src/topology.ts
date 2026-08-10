/**
 * Typed proof-only process topology for Phase 41 (Electron architecture / Windows
 * packaging proof).
 *
 * This module is the single source of truth for every runtime the proof harness may
 * spawn. It is deliberately separate from any product code: the proof must never
 * accumulate domain or database API logic (see `main.ts` and `desktop/proof/README.md`).
 *
 * Contract (D-41-04, D-41-05, T-41-01-01, T-41-01-02):
 * - Every component names an executable, arguments, a loopback endpoint, a readiness
 *   probe, an immutable resource root, a writable app-data root, a log sink and a
 *   single shutdown owner.
 * - `validateTopology` fails CLOSED: any missing field, fixed (pre-allocated) port,
 *   non-loopback bind, writable install path or unknown component is rejected before a
 *   process is started.
 *
 * The port of every component is dynamically allocated by the harness (0 = ask the
 * operating system for a free loopback port). A descriptor that carries a nonzero port
 * is a "fixed packaged port" and is rejected.
 */

/** Loopback-only bind host. No process spawned from this topology may bind a wider interface. */
export const LOOPBACK_HOST = "127.0.0.1" as const;

/** A transport that can carry a loopback port allocated at runtime. */
export type LoopbackTransport = "tcp" | "http";

/** A readiness probe: a loopback client connection must succeed for `tcp`, and an HTTP GET must answer for `http`. */
export interface ReadinessProbe {
  transport: LoopbackTransport;
  /** Path probed when `transport` is `http` (ignored for `tcp`). */
  path?: string;
  /**
   * Overrides the component's allocated port for the probe. Must itself be a loopback
   * endpoint with an explicitly allocated port; a nonzero (fixed) port is rejected.
   */
  port?: ComponentEndpoint;
}

/** A component's network endpoint. `port` is always allocated at runtime by the harness (0 = OS-picked). */
export interface ComponentEndpoint {
  host: string;
  port: number;
}

/** A writable data sink for component logs. Kept separate from the app-data root so tests can assert "log sink present". */
export interface LogSink {
  /** Absolute directory the component's log file lives in. Must be inside the writable app-data root. */
  directory: string;
}

/**
 * Immutable resource root: the read-only install tree from which the process's
 * executable and resources are loaded. Writes must never land here.
 */
export type ResourceRoot = string;

/** Writable app-data root: the only place a spawned process may create mutable data. */
export type AppDataRoot = string;

/**
 * Known proof components. Anything else is rejected at validation time
 * (T-41-01-01: allowlisted components/executables).
 */
export const KNOWN_COMPONENTS = [
  "next",
  "fastapi",
  "agent_service",
  "postgres_pgvector",
  "vector_store",
] as const;
export type ComponentName = (typeof KNOWN_COMPONENTS)[number];

/** Executable source for a component. Never an unvalidated free string. */
export interface ExecutableSource {
  kind: "electron-embedded-node" | "bundled-binary" | "bundled-script";
  /** Path to the executable (a bundled runtime binary or a script entry under the resource root). */
  path: string;
}

/**
 * Process type. `renderer` means the process is launched by Electron itself and is not
 * spawned by the harness; `child` means the harness spawns and owns the lifecycle.
 * Both carry a single explicit shutdown owner (Electron main is the harness itself).
 */
export type ProcessType = "renderer" | "child";

/**
 * Shutdown owner. Exactly one owner must be named for every component.
 * `owner: "harness"` lets the harness terminate the process; a component id delegates
 * to that component's own exit (the owning component must be part of the graph).
 */
export interface ShutdownOwner {
  kind: "harness" | "component";
  component?: ComponentName;
}

/**
 * Typed descriptor for one runtime in the proof topology.
 *
 * A complete, valid graph has exactly the five components in {@link KNOWN_COMPONENTS},
 * each with an executable source, arguments, a loopback endpoint, a dependency list, a
 * readiness probe, an immutable resource root, a writable app-data root, a log sink and
 * a single shutdown owner.
 */
export interface ComponentDescriptor {
  id: ComponentName;
  processType: ProcessType;
  /** Executable source. Never derived from user input at runtime. */
  executable: ExecutableSource;
  /** Static argument vector. The loopback port allocation is appended by the harness; it is never a fixed argument. */
  args: readonly string[];
  /** Loopback endpoint. `port` must be 0 (runtime allocation); a nonzero fixed port is rejected. */
  endpoint: ComponentEndpoint;
  /** Components that must be ready before this one starts. */
  dependsOn: readonly ComponentName[];
  /** Ready check executed against the allocated loopback endpoint. */
  readiness: ReadinessProbe;
  /** Read-only install tree. Writes are rejected here. */
  resourceRoot: ResourceRoot;
  /** The only writable location this component may create mutable data. */
  appDataRoot: AppDataRoot;
  /** Where this component's logs go. Must live under `appDataRoot`. */
  logSink: LogSink;
  /** Single shutdown owner. */
  shutdownOwner: ShutdownOwner;
}

/** A candidate topology under validation. */
export type TopologyCandidate = {
  components: readonly ComponentDescriptor[];
};

export interface TopologyViolation {
  component: string;
  code: string;
  message: string;
}

export interface TopologyValidationResult {
  ok: boolean;
  violations: readonly TopologyViolation[];
}

/**
 * Validation mode.
 * - `default`: five known components, every required field present, no unsafe bindings.
 * - `contract`: validation errors are reduced to a single stable code so tests assert on
 *   the exact fail-closed path. Unknown/duplicate components still surface as distinct
 *   violations because they change the shape of the graph itself.
 */
export type ValidationMode = "default" | "contract";

/** Guards whether a value is one of the known component ids. */
export function isKnownComponent(value: unknown): value is ComponentName {
  return (
    typeof value === "string" &&
    (KNOWN_COMPONENTS as readonly string[]).includes(value)
  );
}

const ALL_COMPONENTS: ComponentName[] = [...KNOWN_COMPONENTS];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isComponentEndpoint(value: unknown): value is ComponentEndpoint {
  return (
    isRecord(value) &&
    typeof value.host === "string" &&
    typeof value.port === "number" &&
    Number.isInteger(value.port) &&
    value.port >= 0 &&
    value.port <= 65535
  );
}

function isReadinessProbe(value: unknown): value is ReadinessProbe {
  if (!isRecord(value)) return false;
  if (value.transport !== "tcp" && value.transport !== "http") return false;
  if (value.path !== undefined && typeof value.path !== "string") return false;
  if (value.port !== undefined && !isComponentEndpoint(value.port)) return false;
  return true;
}

function isExecutableSource(value: unknown): value is ExecutableSource {
  if (!isRecord(value)) return false;
  if (
    value.kind !== "electron-embedded-node" &&
    value.kind !== "bundled-binary" &&
    value.kind !== "bundled-script"
  ) {
    return false;
  }
  return typeof value.path === "string" && value.path.length > 0;
}

function isShutdownOwner(value: unknown): value is ShutdownOwner {
  if (!isRecord(value)) return false;
  if (value.kind !== "harness" && value.kind !== "component") return false;
  if (value.kind === "component") {
    // Structural check only: whether the named owner is a known/in-graph component is
    // reported separately as UNKNOWN-OWNER / UNRESOLVED-OWNER.
    return typeof value.component === "string";
  }
  return value.component === undefined;
}

/** Directory traversal guard used by validation helpers. Normalizes separators to `/` for comparison. */
function isInside(child: string, parent: string): boolean {
  const c = child.replace(/\\/g, "/").replace(/\/+$/, "");
  const p = parent.replace(/\\/g, "/").replace(/\/+$/, "");
  if (p === "") return true;
  return c === p || c.startsWith(p + "/");
}

/**
 * Fail-closed validation of a candidate topology.
 *
 * Rejects before any process starts (T-41-01-01, T-41-01-02):
 * - missing component (the graph must contain all five known components)
 * - duplicate or unknown component ids
 * - missing executable source / args / endpoint / dependencies / probe / roots / log sink / owner
 * - fixed packaged ports (nonzero endpoint or probe port in a descriptor)
 * - non-loopback binds (endpoint host or probe port host outside 127.0.0.1)
 * - writable install paths (log sink or app-data root inside the resource root)
 * - unresolved dependencies or shutdown owners (a `dependsOn`/owner entry not in the graph)
 */
export function validateTopology(
  candidate: TopologyCandidate,
  mode: ValidationMode = "default",
): TopologyValidationResult {
  const violations: TopologyViolation[] = [];
  const push = (component: string, code: string, message: string): void => {
    if (mode === "contract") {
      // Collapse every contract violation into one stable fail-closed signal.
      if (violations.length === 0) {
        violations.push({
          component: "*",
          code: "CONTRACT-REJECTED",
          message: "topology rejected: missing or unsafe contract field",
        });
      }
      return;
    }
    violations.push({ component, code, message });
  };

  const byId = new Map<string, ComponentDescriptor>();
  const ids: string[] = [];

  for (const component of candidate.components) {
    if (!isRecord(component)) {
      push("?", "NOT-A-DESCRIPTOR", "component is not a descriptor object");
      continue;
    }
    const id = component.id;
    if (typeof id !== "string" || !isKnownComponent(id)) {
      push(String(id ?? "?"), "UNKNOWN-COMPONENT", `unknown component id: ${String(id)}`);
      continue;
    }
    if (byId.has(id)) {
      push(id, "DUPLICATE-COMPONENT", `duplicate component: ${id}`);
      continue;
    }
    byId.set(id, component as ComponentDescriptor);
    ids.push(id);
  }

  // Missing components (the graph must be complete, not merely well-formed).
  for (const expected of ALL_COMPONENTS) {
    if (!byId.has(expected)) {
      push(expected, "MISSING-COMPONENT", `missing component: ${expected}`);
    }
  }

  // Field / safety validation on the components we did collect.
  for (const id of ids) {
    const c = byId.get(id)!;

    if (c.processType !== "renderer" && c.processType !== "child") {
      push(id, "MISSING-PROCESS-TYPE", `component ${id}: missing or invalid process type`);
    }
    if (c.executable === undefined || !isExecutableSource(c.executable)) {
      push(id, "MISSING-EXECUTABLE", `component ${id}: missing or invalid executable`);
    }
    if (!Array.isArray(c.args)) {
      push(id, "MISSING-ARGS", `component ${id}: missing arguments`);
    }
    if (!isComponentEndpoint(c.endpoint)) {
      push(id, "MISSING-ENDPOINT", `component ${id}: missing or invalid endpoint`);
    } else {
      if (c.endpoint.host !== LOOPBACK_HOST) {
        push(id, "NON-LOOPBACK-BIND", `component ${id}: endpoint binds ${c.endpoint.host}, only ${LOOPBACK_HOST} is allowed`);
      }
      if (c.endpoint.port !== 0) {
        push(id, "FIXED-PORT", `component ${id}: fixed packaged port ${c.endpoint.port} is rejected; allocate a loopback port at runtime`);
      }
    }
    if (!Array.isArray(c.dependsOn)) {
      push(id, "MISSING-DEPENDENCIES", `component ${id}: missing dependency list`);
    } else {
      for (const dep of c.dependsOn) {
        if (!isKnownComponent(dep)) {
          push(id, "UNKNOWN-DEPENDENCY", `component ${id}: dependency ${String(dep)} is not a known component`);
        } else if (!byId.has(dep)) {
          push(id, "UNRESOLVED-DEPENDENCY", `component ${id}: dependency ${dep} is not part of the topology`);
        }
      }
    }
    if (c.readiness === undefined || !isReadinessProbe(c.readiness)) {
      push(id, "MISSING-PROBE", `component ${id}: missing or invalid readiness probe`);
    } else if (c.readiness.port !== undefined) {
      const probePort = c.readiness.port;
      if (probePort.host !== LOOPBACK_HOST) {
        push(id, "NON-LOOPBACK-BIND", `component ${id}: probe binds ${probePort.host}, only ${LOOPBACK_HOST} is allowed`);
      }
      if (probePort.port !== 0) {
        push(id, "FIXED-PORT", `component ${id}: probe uses fixed port ${probePort.port}; allocate at runtime`);
      }
    }
    if (typeof c.resourceRoot !== "string" || c.resourceRoot.length === 0) {
      push(id, "MISSING-RESOURCE-ROOT", `component ${id}: missing immutable resource root`);
    }
    if (typeof c.appDataRoot !== "string" || c.appDataRoot.length === 0) {
      push(id, "MISSING-APPDATA-ROOT", `component ${id}: missing writable app-data root`);
    }
    if (c.logSink === undefined || !isRecord(c.logSink) || typeof c.logSink.directory !== "string" || c.logSink.directory.length === 0) {
      push(id, "MISSING-LOG-SINK", `component ${id}: missing log sink`);
    } else if (typeof c.resourceRoot === "string" && isInside(c.logSink.directory, c.resourceRoot)) {
      push(id, "WRITABLE-INSTALL-PATH", `component ${id}: log sink writes inside the resource root (${c.resourceRoot})`);
    }
    if (
      typeof c.appDataRoot === "string" &&
      typeof c.resourceRoot === "string" &&
      isInside(c.appDataRoot, c.resourceRoot)
    ) {
      push(id, "WRITABLE-INSTALL-PATH", `component ${id}: app-data root is inside the resource root (${c.resourceRoot})`);
    }
    if (c.shutdownOwner === undefined || !isShutdownOwner(c.shutdownOwner)) {
      push(id, "MISSING-OWNER", `component ${id}: missing or invalid shutdown owner`);
    } else if (c.shutdownOwner.kind === "component") {
      const owner = c.shutdownOwner.component!;
      if (!isKnownComponent(owner)) {
        push(id, "UNKNOWN-OWNER", `component ${id}: shutdown owner ${String(owner)} is not a known component`);
      } else if (!byId.has(owner)) {
        push(id, "UNRESOLVED-OWNER", `component ${id}: shutdown owner ${owner} is not part of the topology`);
      }
    }
  }

  return { ok: violations.length === 0, violations };
}

/**
 * Allocates a loopback port for one component and returns its final endpoint, refusing
 * any host other than the loopback host. `port === 0` asks the operating system to pick
 * a free port; a nonzero port is accepted only when it is the caller's own explicit
 * allocation, never a fixed packaged port baked into a descriptor.
 */
export function allocateEndpoint(endpoint: ComponentEndpoint, port: number): ComponentEndpoint {
  if (endpoint.host !== LOOPBACK_HOST) {
    throw new Error(`refusing to allocate endpoint on non-loopback host ${endpoint.host}`);
  }
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new Error(`invalid loopback port ${port}`);
  }
  return { host: endpoint.host, port };
}
