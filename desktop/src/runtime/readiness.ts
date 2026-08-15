/**
 * Component-specific readiness probes (Phase 43, plan 43-02).
 *
 * Port-open alone is never sufficient evidence (D-43-03, T-43-02-03). Each of
 * the five components has a protocol-level probe:
 *
 *   postgres_pgvector  PostgreSQL wire handshake + `SELECT 1` (SQL roundtrip)
 *   vector_store       HTTP GET /api/v2/heartbeat → 200
 *   fastapi            dependency chain (postgres + vector_store endpoints
 *                      present and ready) AND HTTP GET /api/health → 200
 *   agent_service      HTTP GET /healthz → 200
 *   next               HTTP GET / → 200
 *
 * The PostgreSQL probe is a minimal v3.0 wire-protocol client: startup
 * message, authentication (trust/no-password only — a demanding auth method is
 * reported NOT ready, never probed blindly), a `SELECT 1` query roundtrip and
 * a clean terminate. Everything is bounded by explicit timeouts; a probe never
 * hangs the graph.
 *
 * Transports are injectable so unit tests can fake protocol responses without
 * real databases; `nodeReadinessTransport()` is the production implementation.
 */
import { createConnection, type Socket } from "node:net";
import type { ComponentEndpoint, RuntimeComponent } from "./types";

/** Poll interval used by waitForReadiness. */
export const READINESS_POLL_INTERVAL_MS = 200;
/** Default single-probe timeout. */
export const READINESS_PROBE_TIMEOUT_MS = 2_000;

/** Postgres probe defaults, aligned with docker-compose dev credentials. */
export const POSTGRES_PROBE_USER = "novelmind" as const;
export const POSTGRES_PROBE_DATABASE = "novelmind" as const;
export const POSTGRES_PROBE_QUERY = "SELECT 1" as const;

/** HTTP GET probe: resolves with the status code, or null when unreachable. */
export type HttpStatusTransport = (
  host: string,
  port: number,
  path: string,
  timeoutMs: number,
) => Promise<number | null>;

export interface PostgresProbeOptions {
  host: string;
  port: number;
  user: string;
  database: string;
  query: string;
  timeoutMs: number;
}

/** PostgreSQL wire-probe: resolves true only after a real SQL roundtrip. */
export type PostgresTransport = (options: PostgresProbeOptions) => Promise<boolean>;

/** Injectable transport bundle used by every probe. */
export interface ReadinessTransport {
  httpStatus: HttpStatusTransport;
  postgresReady: PostgresTransport;
}

/** Endpoints of components already confirmed ready (dependency chain). */
export type DependencyEndpoints = ReadonlyMap<RuntimeComponent, ComponentEndpoint>;

export interface ReadinessContext {
  component: RuntimeComponent;
  endpoint: ComponentEndpoint;
  dependencies: DependencyEndpoints;
  transport: ReadinessTransport;
  timeoutMs: number;
}

export type ComponentProbe = (context: ReadinessContext) => Promise<boolean>;

/** A required dependency must be present in the chain before a probe runs. */
export function requireDependency(
  ctx: ReadinessContext,
  dependency: RuntimeComponent,
): ComponentEndpoint | null {
  return ctx.dependencies.get(dependency) ?? null;
}

async function httpProbe(
  ctx: ReadinessContext,
  path: string,
): Promise<boolean> {
  const status = await ctx.transport.httpStatus(
    ctx.endpoint.host,
    ctx.endpoint.port,
    path,
    ctx.timeoutMs,
  );
  return status === 200;
}

async function postgresProbe(ctx: ReadinessContext): Promise<boolean> {
  return ctx.transport.postgresReady({
    host: ctx.endpoint.host,
    port: ctx.endpoint.port,
    user: POSTGRES_PROBE_USER,
    database: POSTGRES_PROBE_DATABASE,
    query: POSTGRES_PROBE_QUERY,
    timeoutMs: ctx.timeoutMs,
  });
}

/**
 * FastAPI is only "ready" when its dependency chain (PostgreSQL + vector store)
 * is present AND its own health endpoint answers 200 (D-43-03, T-43-02-03).
 */
async function fastapiProbe(ctx: ReadinessContext): Promise<boolean> {
  if (requireDependency(ctx, "postgres_pgvector") === null) return false;
  if (requireDependency(ctx, "vector_store") === null) return false;
  return httpProbe(ctx, "/api/health");
}

/** The canonical per-component probe table (T-43-02-03). */
export const READINESS_PROBES: Readonly<Record<RuntimeComponent, ComponentProbe>> = {
  postgres_pgvector: postgresProbe,
  vector_store: (ctx) => httpProbe(ctx, "/api/v2/heartbeat"),
  fastapi: fastapiProbe,
  agent_service: (ctx) => httpProbe(ctx, "/healthz"),
  next: (ctx) => httpProbe(ctx, "/"),
};

/**
 * Runs one protocol-level readiness probe for a component. Never throws; a
 * probe that fails or times out resolves `false` so callers can react to typed
 * state instead of crashes.
 */
export function checkComponentReadiness(
  component: RuntimeComponent,
  endpoint: ComponentEndpoint,
  dependencies: DependencyEndpoints,
  transport: ReadinessTransport,
  timeoutMs: number = READINESS_PROBE_TIMEOUT_MS,
): Promise<boolean> {
  const probe = READINESS_PROBES[component];
  return probe({
    component,
    endpoint,
    dependencies,
    transport,
    timeoutMs,
  });
}

export interface ReadinessWaitOptions {
  /** Total budget for the wait. */
  deadlineMs: number;
  /** Poll interval. Defaults to READINESS_POLL_INTERVAL_MS. */
  intervalMs?: number;
}

/**
 * Polls a component's protocol probe until it succeeds, the budget is
 * exhausted, or the component process has exited (adapter reports no live
 * process). Bounded retries with an explicit deadline — never sleeps blindly.
 */
export async function waitForReadiness(
  component: RuntimeComponent,
  endpoint: ComponentEndpoint,
  dependencies: DependencyEndpoints,
  transport: ReadinessTransport,
  options: ReadinessWaitOptions,
  isLive: () => boolean = () => true,
): Promise<boolean> {
  const deadline = Date.now() + options.deadlineMs;
  const interval = options.intervalMs ?? READINESS_POLL_INTERVAL_MS;
  while (Date.now() < deadline) {
    if (!isLive()) return false;
    if (await checkComponentReadiness(component, endpoint, dependencies, transport)) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  return false;
}

// ── PostgreSQL v3.0 wire probe (real transport) ─────────────────────────────

const PG_PROTOCOL_VERSION_3_0 = 196608;
const PG_AUTHENTICATION_OK = 0;

function pgStartupMessage(user: string, database: string): Buffer {
  const params = `user\x00${user}\x00database\x00${database}\x00\x00`;
  const length = 4 + 4 + Buffer.byteLength(params, "utf8");
  const buf = Buffer.alloc(length);
  buf.writeInt32BE(length, 0);
  buf.writeInt32BE(PG_PROTOCOL_VERSION_3_0, 4);
  buf.write(params, 8, "utf8");
  return buf;
}

function pgQueryMessage(query: string): Buffer {
  const queryBytes = Buffer.from(query, "utf8");
  const length = 4 + queryBytes.length + 1;
  const buf = Buffer.alloc(length);
  buf.writeInt32BE(length, 0);
  queryBytes.copy(buf, 4);
  buf.writeUInt8(0, 4 + queryBytes.length);
  return buf;
}

function pgTerminateMessage(): Buffer {
  return Buffer.from([0x00, 0x00, 0x00, 0x04]);
}

function postgresProbeTransport(options: PostgresProbeOptions): Promise<boolean> {
  const { host, port, user, database, query, timeoutMs } = options;
  return new Promise<boolean>((resolve) => {
    const socket: Socket = createConnection({ host, port });
    let settled = false;
    let buffer = Buffer.alloc(0);
    let phase: "auth" | "query" = "auth";
    let sawAuthOk = false;

    const finish = (ok: boolean): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      resolve(ok);
    };
    const onError = (): void => finish(false);

    const timer = setTimeout(onError, timeoutMs);
    socket.setTimeout(timeoutMs);
    socket.once("error", onError);
    socket.once("timeout", onError);
    socket.once("close", onError);

    socket.once("connect", () => {
      socket.write(pgStartupMessage(user, database));
    });

    socket.on("data", (chunk: Buffer) => {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.length >= 5) {
        const length = buffer.readInt32BE(1);
        if (buffer.length < 1 + length) break; // wait for the full message
        const type = String.fromCharCode(buffer.readUInt8(0));
        const body = buffer.subarray(5, 1 + length);
        buffer = buffer.subarray(1 + length);

        if (type === "E") {
          finish(false); // ErrorResponse
          return;
        }
        if (phase === "auth") {
          if (type === "R") {
            const code = body.readInt32BE(0);
            if (code !== PG_AUTHENTICATION_OK) {
              // Scram/md5/cleartext demands credentials we never hold: not ready.
              finish(false);
              return;
            }
            sawAuthOk = true;
          } else if (type === "Z" && sawAuthOk) {
            phase = "query";
            socket.write(pgQueryMessage(query));
          }
          // Ignore ParameterStatus / BackendKeyData during auth.
        } else if (type === "Z") {
          // CommandComplete (C) observed before the trailing ReadyForQuery —
          // the SQL roundtrip succeeded. Send a clean terminate then answer.
          socket.write(pgTerminateMessage());
          finish(true);
          return;
        }
      }
    });
  });
}

async function httpStatusTransport(
  host: string,
  port: number,
  path: string,
  timeoutMs: number,
): Promise<number | null> {
  try {
    const response = await fetch(`http://${host}:${port}${path}`, {
      signal: AbortSignal.timeout(timeoutMs),
      redirect: "manual",
    });
    return response.status;
  } catch {
    return null;
  }
}

/** Production transport: real HTTP + PostgreSQL wire probes. */
export function nodeReadinessTransport(): ReadinessTransport {
  return {
    httpStatus: httpStatusTransport,
    postgresReady: postgresProbeTransport,
  };
}

/** A transport that never succeeds — used to prove bounded failure. */
export function deadTransport(): ReadinessTransport {
  return {
    httpStatus: async () => null,
    postgresReady: async () => false,
  };
}
