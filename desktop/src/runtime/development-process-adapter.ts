/**
 * DevelopmentProcessAdapter (D-43-02).
 *
 * Reuses the existing local developer entrypoints — backend venv uvicorn,
 * frontend `next dev`, `agent-service/start.mjs` and the Docker images already
 * declared in `docker-compose.yml` — but ALWAYS allocates a dynamic loopback
 * port per component and injects it into the process. Fixed developer ports
 * (8010/3005/3100/5432/8001) are never hard-coded here: the topology contract
 * rejects fixed ports (port 0 = OS allocation).
 *
 * This adapter is developer convenience; it may resolve bare command names from
 * PATH. It is NOT the production path — the packaged adapter is.
 */
import path from "node:path";
import {
  RUNTIME_COMPONENTS,
  RUNTIME_ERROR_CODES,
  RuntimeError,
  type AdapterBudgets,
  type ComponentLaunch,
  type RuntimeComponent,
} from "./types";
import { BaseProcessAdapter } from "./base-process-adapter";
import type { ProcessOperations } from "./process-operations";

export interface DevelopmentPaths {
  /** Repo root used to resolve dev entrypoints. Defaults to process.cwd(). */
  repoRoot: string;
  /** Absolute backend venv python.exe. Resolved under backend/ if omitted. */
  backendPython?: string;
  /** Absolute node executable. Defaults to "node" (resolved from PATH by the OS). */
  nodeCommand?: string;
  /** Absolute docker executable. Defaults to "docker" (resolved from PATH by the OS). */
  dockerCommand?: string;
  /**
   * Main-owned local-auth HMAC secret source (44-03). Injected into the agent
   * service environment as NOVELMIND_LOCAL_AUTH_SECRET at spawn so the agent
   * service enforces audience/expiry-bound local session tokens on every
   * inbound run request. The value is read at spawn time — the secret rotates
   * on runtime restart and the next launch picks up the fresh secret. The
   * secret itself never leaves the main process (T-44-02-01).
   */
  localAuthSecret?: () => string | null;
}

/** DevelopmentPaths after defaults have been applied. */
interface ResolvedDevelopmentPaths {
  repoRoot: string;
  backendPython: string | undefined;
  nodeCommand: string;
  dockerCommand: string;
  localAuthSecret: (() => string | null) | undefined;
}

const DEV_AGENT_GATEWAY_TOKEN = "dev-agent-gateway-token-local";

export class DevelopmentProcessAdapter extends BaseProcessAdapter {
  readonly mode = "development" as const;
  readonly launchable: readonly RuntimeComponent[] = [...RUNTIME_COMPONENTS];

  private readonly paths: ResolvedDevelopmentPaths;

  constructor(
    ops: ProcessOperations,
    budgets?: Partial<AdapterBudgets>,
    paths?: Partial<DevelopmentPaths>,
  ) {
    super(ops, budgets);
    this.paths = {
      repoRoot: paths?.repoRoot ?? process.cwd(),
      backendPython: paths?.backendPython,
      nodeCommand: paths?.nodeCommand ?? "node",
      dockerCommand: paths?.dockerCommand ?? "docker",
      localAuthSecret: paths?.localAuthSecret,
    };
  }

  protected launchConfig(component: RuntimeComponent): ComponentLaunch {
    switch (component) {
      case "postgres_pgvector":
        return this.pgLaunch();
      case "vector_store":
        return this.vectorLaunch();
      case "fastapi":
        return this.fastapiLaunch();
      case "agent_service":
        return this.agentLaunch();
      case "next":
        return this.nextLaunch();
    }
  }

  private pgLaunch(): ComponentLaunch {
    return {
      command: this.paths.dockerCommand,
      args: [
        "run",
        "--rm",
        "--name",
        `novelmind-dev-pg-${process.pid}`,
        "-e",
        "POSTGRES_DB=novelmind",
        "-e",
        "POSTGRES_USER=novelmind",
        "-e",
        "POSTGRES_PASSWORD=novelmind",
        "pgvector/pgvector:pg16",
      ],
      portVia: { kind: "arg", flag: "-p", valueSuffix: ":5432" },
      probe: { transport: "tcp" },
    };
  }

  private vectorLaunch(): ComponentLaunch {
    return {
      command: this.paths.dockerCommand,
      args: [
        "run",
        "--rm",
        "--name",
        `novelmind-dev-chroma-${process.pid}`,
        "-e",
        "CHROMA_SERVER_HOST=0.0.0.0",
        "-e",
        "CHROMA_SERVER_HTTP_PORT=8000",
        "chromadb/chroma:latest",
      ],
      portVia: { kind: "arg", flag: "-p", valueSuffix: ":8000" },
      probe: { transport: "http", path: "/api/v2/heartbeat" },
    };
  }

  private fastapiLaunch(): ComponentLaunch {
    const pgEndpoint = this.endpoint("postgres_pgvector");
    const vectorEndpoint = this.endpoint("vector_store");
    const env: Record<string, string> = {};
    if (pgEndpoint !== null) {
      // Dynamic DB URL injected into the backend (config.py NOVELMIND_ prefix).
      env["NOVELMIND_DATABASE_URL"] =
        `postgresql+asyncpg://novelmind:novelmind@${pgEndpoint.host}:${pgEndpoint.port}/novelmind`;
    }
    if (vectorEndpoint !== null) {
      // Dynamic Chroma host/port injected (vector_store.py env channel, 43-02).
      env["NOVELMIND_VECTOR_HOST"] = vectorEndpoint.host;
      env["NOVELMIND_VECTOR_PORT"] = String(vectorEndpoint.port);
    }
    return {
      command: this.resolveBackendPython(),
      args: ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1"],
      portVia: { kind: "arg", flag: "--port" },
      cwd: path.join(this.paths.repoRoot, "backend"),
      env,
      probe: { transport: "http", path: "/health" },
    };
  }

  private agentLaunch(): ComponentLaunch {
    const fastapiEndpoint = this.endpoint("fastapi");
    const baseUrl =
      fastapiEndpoint !== null
        ? `http://${fastapiEndpoint.host}:${fastapiEndpoint.port}`
        : "http://127.0.0.1:8010"; // dev fallback default (Makefile)
    const env: Record<string, string> = {
      NOVELMIND_GATEWAY_TOKEN: DEV_AGENT_GATEWAY_TOKEN,
      FASTAPI_BASE_URL: baseUrl,
    };
    const secret = this.paths.localAuthSecret?.() ?? null;
    // Fail-closed local session auth (44-03): when main owns local-auth
    // material, the agent service MUST verify the audience-bound session token
    // on every inbound request. No secret → the agent service's guard rejects
    // (its fail-closed default), so desktop runs are never unauthenticated.
    if (secret !== null && secret !== "") {
      env["NOVELMIND_LOCAL_AUTH_SECRET"] = secret;
    }
    return {
      command: this.paths.nodeCommand,
      args: ["start.mjs"],
      cwd: path.join(this.paths.repoRoot, "agent-service"),
      env,
      portVia: { kind: "env", name: "PORT" },
      probe: { transport: "http", path: "/health" },
    };
  }

  private nextLaunch(): ComponentLaunch {
    const nextBin = path.join(
      this.paths.repoRoot,
      "frontend",
      "node_modules",
      "next",
      "dist",
      "bin",
      "next",
    );
    return {
      command: this.paths.nodeCommand,
      args: [nextBin, "dev", "--hostname", "127.0.0.1"],
      portVia: { kind: "arg", flag: "--port" },
      cwd: path.join(this.paths.repoRoot, "frontend"),
      probe: { transport: "http", path: "/" },
    };
  }

  private resolveBackendPython(): string {
    if (this.paths.backendPython !== undefined) return this.paths.backendPython;
    const repo = this.paths.repoRoot;
    const candidates = [
      path.join(repo, "backend", "venv", "Scripts", "python.exe"),
      path.join(repo, "backend", ".venv", "Scripts", "python.exe"),
    ];
    for (const candidate of candidates) {
      if (this.ops.exists(candidate)) return candidate;
    }
    throw new RuntimeError(
      RUNTIME_ERROR_CODES.EXECUTABLE_NOT_FOUND,
      "backend python venv not found for development adapter",
      "fastapi",
    );
  }
}
