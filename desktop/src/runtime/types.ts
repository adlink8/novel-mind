/**
 * DesktopRuntime contract types (Phase 43, plan 43-01).
 *
 * Pure type/constant vocabulary shared by the runtime orchestrator
 * (`desktop-runtime.ts`), both process adapters and the Electron main process.
 * This module MUST stay free of Node, Electron and backend-domain imports; the
 * static authority-boundary scan in plan 43-01 Task 3 enforces that.
 *
 * Contract (D-43-01, D-43-02, D-43-03, D-43-04, D-43-08, T-43-01-01, T-43-01-02):
 * - `DesktopRuntime` exposes exactly four lifecycle methods: `ensureReady`,
 *   `status`, `restart`, `shutdown`.
 * - Runtime state is a typed state machine:
 *   `stopped | starting | migrating | ready | degraded | failed | stopping`.
 * - `ready` is only reachable when every component is ready; "ready with a
 *   failed dependency" is unrepresentable (guarded in `desktop-runtime.ts`).
 * - Snapshots carry component state/readiness/endpoints only — no secrets, no
 *   PIDs, no executable paths, no environment, no command lines.
 * - Every endpoint is a dynamically allocated loopback endpoint; a fixed port
 *   is rejected by construction (port 0 = OS allocation, topology.ts contract).
 * - The desktop runtime is never a domain or persistence authority; it imports
 *   nothing from backend domain modules.
 */

export const RUNTIME_COMPONENTS = [
  "next",
  "fastapi",
  "agent_service",
  "postgres_pgvector",
  "vector_store",
] as const;

export type RuntimeComponent = (typeof RUNTIME_COMPONENTS)[number];

export function isRuntimeComponent(value: unknown): value is RuntimeComponent {
  return (
    typeof value === "string" && (RUNTIME_COMPONENTS as readonly string[]).includes(value)
  );
}

/** Loopback-only bind host. No process spawned by this runtime may bind wider. */
export const LOOPBACK_HOST = "127.0.0.1" as const;

export interface ComponentEndpoint {
  host: typeof LOOPBACK_HOST;
  /** OS-allocated loopback port; always > 0 after allocation. */
  port: number;
}

/**
 * Components that must be ready before a given component starts (D-43-03):
 * persistence/vector -> backend -> agent_service -> next.
 */
export const COMPONENT_DEPENDENCIES: Readonly<
  Record<RuntimeComponent, readonly RuntimeComponent[]>
> = {
  postgres_pgvector: [],
  vector_store: [],
  fastapi: ["postgres_pgvector", "vector_store"],
  agent_service: ["fastapi"],
  next: ["fastapi", "agent_service"],
};

/** Dependency-ordered startup sequence (topological). */
export const RUNTIME_START_ORDER: readonly RuntimeComponent[] = [
  "postgres_pgvector",
  "vector_store",
  "fastapi",
  "agent_service",
  "next",
];

export const RUNTIME_STATES = [
  "stopped",
  "starting",
  "migrating",
  "ready",
  "degraded",
  "failed",
  "stopping",
] as const;
export type RuntimeState = (typeof RUNTIME_STATES)[number];

export const COMPONENT_STATES = ["stopped", "starting", "ready", "failed", "stopping"] as const;
export type ComponentState = (typeof COMPONENT_STATES)[number];

/**
 * Stable redacted error codes. `code` is the only stable signal; `message` is a
 * fixed literal and never carries paths, environment or command lines.
 */
export const RUNTIME_ERROR_CODES = {
  COMPONENT_UNKNOWN: "COMPONENT_UNKNOWN",
  ILLEGAL_TRANSITION: "ILLEGAL_TRANSITION",
  READY_INVARIANT_VIOLATION: "READY_INVARIANT_VIOLATION",
  EXECUTABLE_NOT_FOUND: "EXECUTABLE_NOT_FOUND",
  SPAWN_FAILED: "SPAWN_FAILED",
  START_TIMEOUT: "START_TIMEOUT",
  EXIT_EARLY: "EXIT_EARLY",
  STOP_KILL_FAILED: "STOP_KILL_FAILED",
  MIGRATION_FAILED: "MIGRATION_FAILED",
  UNSUPPORTED_IN_PACKAGED: "UNSUPPORTED_IN_PACKAGED",
  BUSY: "BUSY",
  INTERNAL: "INTERNAL",
} as const;
export type RuntimeErrorCode = (typeof RUNTIME_ERROR_CODES)[keyof typeof RUNTIME_ERROR_CODES];

/** Redacted failure payload used by snapshots. */
export interface RedactedError {
  code: RuntimeErrorCode;
  message: string;
}

export class RuntimeError extends Error {
  readonly code: RuntimeErrorCode;
  readonly component?: RuntimeComponent;

  constructor(code: RuntimeErrorCode, message: string, component?: RuntimeComponent) {
    super(message);
    this.name = "RuntimeError";
    this.code = code;
    if (component !== undefined) this.component = component;
  }

  /** Stable, secret-free payload. */
  redacted(): RedactedError {
    return { code: this.code, message: this.message };
  }
}

export interface ComponentSnapshot {
  id: RuntimeComponent;
  state: ComponentState;
  ready: boolean;
  endpoint: ComponentEndpoint | null;
  lastError: RedactedError | null;
}

export interface RuntimeSnapshot {
  /** Snapshot schema version. */
  version: 1;
  state: RuntimeState;
  /** True only when state is "ready" (every component ready). */
  ready: boolean;
  components: readonly ComponentSnapshot[];
  /** ISO timestamp of the last successful full startup, or null. */
  startedAt: string | null;
  lastError: RedactedError | null;
}

export interface ShutdownReport {
  stopped: readonly RuntimeComponent[];
  failed: readonly { component: RuntimeComponent; error: RedactedError }[];
}

export type AdapterMode = "development" | "packaged";

export interface AdapterBudgets {
  /** Max time to wait for a component to become ready after spawn. */
  startTimeoutMs: number;
  /** Graceful drain window before the process tree is force-killed. */
  drainMs: number;
  /** Max time to wait for the tree to die after force-kill. */
  killMs: number;
}

export type ProbeTransport = "tcp" | "http";

export interface ProbeConfig {
  transport: ProbeTransport;
  /** HTTP path probed when transport is "http". */
  path?: string;
}

export interface ComponentLaunch {
  /** Executable (absolute path preferred) the adapter spawns. */
  command: string;
  args: readonly string[];
  cwd?: string;
  env?: Readonly<Record<string, string>>;
  /** How the OS-allocated loopback port is handed to the process. */
  portVia: { kind: "arg"; flag: string; valueSuffix?: string } | { kind: "env"; name: string };
  probe: ProbeConfig;
}

export interface StartedProcess {
  component: RuntimeComponent;
  endpoint: ComponentEndpoint;
}

/** Migration is injected, never owned: the runtime is not a database authority. */
export interface MigrationGate {
  needsMigration(): Promise<boolean>;
  run(): Promise<void>;
}

export function noMigrationGate(): MigrationGate {
  return { needsMigration: async () => false, run: async () => undefined };
}

/**
 * Shared process-adapter contract (D-43-02). Both adapters implement this exact
 * surface; the runtime depends on the interface, never on process details.
 * PID/executable internals stay private inside implementations.
 */
export interface ProcessAdapter {
  readonly mode: AdapterMode;
  /** Components the adapter may launch; anything else fails closed. */
  readonly launchable: readonly RuntimeComponent[];
  /** Launch a component and wait for readiness. Idempotent while running. */
  start(component: RuntimeComponent): Promise<StartedProcess>;
  /** Stop a component's owned process tree (drain then force-kill). Idempotent. */
  stop(component: RuntimeComponent): Promise<void>;
  /** Current endpoint of a running component, or null. */
  endpoint(component: RuntimeComponent): ComponentEndpoint | null;
  /** Whether the adapter owns a live process for the component. */
  isRunning(component: RuntimeComponent): boolean;
  /** Safe diagnostic label — never a raw executable path. */
  describe(component: RuntimeComponent): string;
  /** Subscribe to UNINTENTIONAL exits of a launched component. */
  onExit(component: RuntimeComponent, listener: (code: number | null) => void): () => void;
}
