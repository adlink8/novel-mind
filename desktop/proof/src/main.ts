/**
 * Proof harness entry for Phase 41 (Electron architecture / Windows packaging proof).
 *
 * PROOF-ONLY. This process:
 * - builds the single proof topology fixture for the five runtimes (next, fastapi,
 *   agent_service, postgres_pgvector, vector_store),
 * - validates it fail-closed via `validateTopology`,
 * - allocates loopback ports and computes a deterministic startup order,
 * - exits nonzero with a rejected-topology report when any contract field is unsafe.
 *
 * It contains NO domain or database API and no product UI logic. If the Phase 41
 * GO/NO-GO decision changes the packaging adapter, this file is disposable.
 *
 * Run: `npm start` (equivalent to `node src/main.ts`).
 */

import { LOOPBACK_HOST, allocateEndpoint, validateTopology } from "./topology.ts";
import type { ComponentDescriptor, ComponentName } from "./topology.ts";

/** Where a bundled binary or script lives. Proof placeholder — replaced by the packaging matrix. */
const PROOF_RESOURCE_ROOT = "C:/novelmind-proof/resources";

/** The only writable location a spawned proof process may create data. */
const PROOF_APP_DATA_ROOT = "C:/Users/proof-user/AppData/Roaming/novelmind-proof";

/** Logs live under the writable app-data root, never inside the resource root. */
const PROOF_LOG_DIR = `${PROOF_APP_DATA_ROOT}/logs`;

/**
 * The five-component proof topology (D-41-04, D-41-05). Every endpoint host is the
 * loopback host and every port is 0 — allocated by the harness at runtime, never a
 * fixed packaged port. Executables are allowlisted kinds; nothing here is derived from
 * user input (T-41-01-01).
 */
export function buildProofTopology(): readonly ComponentDescriptor[] {
  return [
    {
      id: "postgres_pgvector",
      processType: "child",
      executable: { kind: "bundled-binary", path: `${PROOF_RESOURCE_ROOT}/pgsql/bin/postgres.exe` },
      args: ["-D", `${PROOF_APP_DATA_ROOT}/pgdata`],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: [],
      readiness: { transport: "tcp" },
      resourceRoot: PROOF_RESOURCE_ROOT,
      appDataRoot: PROOF_APP_DATA_ROOT,
      logSink: { directory: `${PROOF_LOG_DIR}/postgres` },
      shutdownOwner: { kind: "harness" },
    },
    {
      id: "vector_store",
      processType: "child",
      executable: { kind: "bundled-binary", path: `${PROOF_RESOURCE_ROOT}/vector-store/bin/vector-store.exe` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: ["postgres_pgvector"],
      readiness: { transport: "http", path: "/health" },
      resourceRoot: PROOF_RESOURCE_ROOT,
      appDataRoot: PROOF_APP_DATA_ROOT,
      logSink: { directory: `${PROOF_LOG_DIR}/vector-store` },
      shutdownOwner: { kind: "harness" },
    },
    {
      id: "fastapi",
      processType: "child",
      executable: { kind: "bundled-script", path: `${PROOF_RESOURCE_ROOT}/backend/run-backend.mjs` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: ["postgres_pgvector", "vector_store"],
      readiness: { transport: "http", path: "/health" },
      resourceRoot: PROOF_RESOURCE_ROOT,
      appDataRoot: PROOF_APP_DATA_ROOT,
      logSink: { directory: `${PROOF_LOG_DIR}/fastapi` },
      shutdownOwner: { kind: "harness" },
    },
    {
      id: "agent_service",
      processType: "child",
      executable: { kind: "electron-embedded-node", path: `${PROOF_RESOURCE_ROOT}/agent-service/start.mjs` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: ["fastapi"],
      readiness: { transport: "http", path: "/health" },
      resourceRoot: PROOF_RESOURCE_ROOT,
      appDataRoot: PROOF_APP_DATA_ROOT,
      logSink: { directory: `${PROOF_LOG_DIR}/agent-service` },
      shutdownOwner: { kind: "harness" },
    },
    {
      id: "next",
      processType: "renderer",
      executable: { kind: "electron-embedded-node", path: `${PROOF_RESOURCE_ROOT}/next-standalone/server.js` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: ["fastapi", "agent_service"],
      readiness: { transport: "http", path: "/" },
      resourceRoot: PROOF_RESOURCE_ROOT,
      appDataRoot: PROOF_APP_DATA_ROOT,
      logSink: { directory: `${PROOF_LOG_DIR}/next` },
      shutdownOwner: { kind: "harness" },
    },
  ];
}

/**
 * Deterministic startup order honoring `dependsOn` (a dependency always precedes the
 * component that requires it). Cycle-free by construction in this fixture; a cyclic
 * dependency list is a topology error the harness surfaces instead of hanging.
 */
export function startupOrder(components: readonly ComponentDescriptor[]): ComponentName[] {
  const byId = new Map<string, ComponentDescriptor>();
  for (const c of components) byId.set(c.id, c);
  const ordered: ComponentName[] = [];
  const visited = new Set<string>();
  const visiting = new Set<string>();

  const visit = (id: ComponentName): void => {
    if (visited.has(id)) return;
    if (visiting.has(id)) {
      throw new Error(`cyclic dependency detected at ${id}`);
    }
    visiting.add(id);
    const c = byId.get(id);
    if (c !== undefined) {
      for (const dep of c.dependsOn) visit(dep);
    }
    visiting.delete(id);
    visited.add(id);
    ordered.push(id);
  };

  for (const c of components) visit(c.id);
  return ordered;
}

/** Prints the validated startup plan. Pure reporting — no process is spawned by this proof harness. */
function printStartupPlan(components: readonly ComponentDescriptor[]): void {
  const order = startupOrder(components);
  console.log("[proof] topology VALID — five-component graph accepted (D-41-04, D-41-05)");
  console.log("[proof] startup order:");
  for (const id of order) {
    const c = components.find((x) => x.id === id)!;
    const port = allocateEndpoint(c.endpoint, 0).port;
    console.log(
      `  ${id.padEnd(18)} ${c.processType.padEnd(8)} http://${LOOPBACK_HOST}:${port} ${c.readiness.transport}`,
    );
  }
  console.log("[proof] ports are dynamically allocated loopback ports; shutdown owner for every child is the harness.");
}

function fail(reason: string): never {
  console.error(`[proof] TOPOLOGY REJECTED: ${reason}`);
  process.exitCode = 1;
  throw new Error(`topology rejected: ${reason}`);
}

function main(): void {
  const components = buildProofTopology();
  const result = validateTopology({ components });
  if (!result.ok) {
    for (const v of result.violations) {
      console.error(`  [${v.code}] ${v.message}`);
    }
    fail("topology is incomplete or unsafe — no process will be started (fail-closed)");
  }
  printStartupPlan(components);
}

main();
