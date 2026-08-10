/**
 * Fail-closed contract tests for the proof topology (Plan 41-01 Task 2/3).
 *
 * Positive: one complete five-component graph is accepted.
 * Negative: every missing field, fixed port, non-loopback bind, writable install path
 * and unknown component is rejected BEFORE any process starts.
 */

import { describe, expect, it } from "vitest";
import {
  LOOPBACK_HOST,
  KNOWN_COMPONENTS,
  allocateEndpoint,
  isKnownComponent,
  validateTopology,
  type ComponentDescriptor,
  type TopologyCandidate,
} from "../src/topology.ts";

const RESOURCE_ROOT = "C:/novelmind-proof/resources";
const APP_DATA_ROOT = "C:/Users/proof-user/AppData/Roaming/novelmind-proof";

/** A complete, safe, valid five-component topology fixture. */
function validTopology(): ComponentDescriptor[] {
  return [
    {
      id: "postgres_pgvector",
      processType: "child",
      executable: { kind: "bundled-binary", path: `${RESOURCE_ROOT}/pgsql/bin/postgres.exe` },
      args: ["-D", `${APP_DATA_ROOT}/pgdata`],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: [],
      readiness: { transport: "tcp" },
      resourceRoot: RESOURCE_ROOT,
      appDataRoot: APP_DATA_ROOT,
      logSink: { directory: `${APP_DATA_ROOT}/logs/postgres` },
      shutdownOwner: { kind: "harness" },
    },
    {
      id: "vector_store",
      processType: "child",
      executable: { kind: "bundled-binary", path: `${RESOURCE_ROOT}/vector-store/bin/vector-store.exe` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: ["postgres_pgvector"],
      readiness: { transport: "http", path: "/health" },
      resourceRoot: RESOURCE_ROOT,
      appDataRoot: APP_DATA_ROOT,
      logSink: { directory: `${APP_DATA_ROOT}/logs/vector-store` },
      shutdownOwner: { kind: "harness" },
    },
    {
      id: "fastapi",
      processType: "child",
      executable: { kind: "bundled-script", path: `${RESOURCE_ROOT}/backend/run-backend.mjs` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: ["postgres_pgvector", "vector_store"],
      readiness: { transport: "http", path: "/health" },
      resourceRoot: RESOURCE_ROOT,
      appDataRoot: APP_DATA_ROOT,
      logSink: { directory: `${APP_DATA_ROOT}/logs/fastapi` },
      shutdownOwner: { kind: "harness" },
    },
    {
      id: "agent_service",
      processType: "child",
      executable: { kind: "electron-embedded-node", path: `${RESOURCE_ROOT}/agent-service/start.mjs` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: ["fastapi"],
      readiness: { transport: "http", path: "/health" },
      resourceRoot: RESOURCE_ROOT,
      appDataRoot: APP_DATA_ROOT,
      logSink: { directory: `${APP_DATA_ROOT}/logs/agent-service` },
      shutdownOwner: { kind: "harness" },
    },
    {
      id: "next",
      processType: "renderer",
      executable: { kind: "electron-embedded-node", path: `${RESOURCE_ROOT}/next-standalone/server.js` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: ["fastapi", "agent_service"],
      readiness: { transport: "http", path: "/" },
      resourceRoot: RESOURCE_ROOT,
      appDataRoot: APP_DATA_ROOT,
      logSink: { directory: `${APP_DATA_ROOT}/logs/next` },
      shutdownOwner: { kind: "harness" },
    },
  ];
}

/** Deep clone the fixture so each test mutates its own copy. */
function clone(topo: ComponentDescriptor[]): ComponentDescriptor[] {
  return JSON.parse(JSON.stringify(topo)) as ComponentDescriptor[];
}

function candidate(topo: ComponentDescriptor[]): TopologyCandidate {
  return { components: topo };
}

function componentOf(topo: ComponentDescriptor[], id: string): ComponentDescriptor {
  const c = topo.find((x) => x.id === id);
  if (c === undefined) throw new Error(`fixture missing ${id}`);
  return c;
}

function violationCodes(topo: TopologyCandidate): string[] {
  return validateTopology(topo).violations.map((v) => v.code);
}

describe("topology: positive contract", () => {
  it("accepts one complete five-component graph", () => {
    const result = validateTopology(candidate(validTopology()));
    expect(result.ok).toBe(true);
    expect(result.violations).toHaveLength(0);
  });

  it("exposes exactly the five known components", () => {
    expect(KNOWN_COMPONENTS).toEqual([
      "next",
      "fastapi",
      "agent_service",
      "postgres_pgvector",
      "vector_store",
    ]);
  });

  it("guards known-component membership", () => {
    expect(isKnownComponent("next")).toBe(true);
    expect(isKnownComponent("vector_store")).toBe(true);
    expect(isKnownComponent("electron")).toBe(false);
    expect(isKnownComponent(42)).toBe(false);
  });

  it("allocates a loopback port and refuses a non-loopback host", () => {
    expect(allocateEndpoint({ host: LOOPBACK_HOST, port: 0 }, 0).port).toBe(0);
    expect(allocateEndpoint({ host: LOOPBACK_HOST, port: 0 }, 41000).port).toBe(41000);
    expect(() => allocateEndpoint({ host: "0.0.0.0", port: 0 }, 0)).toThrow(/non-loopback/);
    expect(() => allocateEndpoint({ host: LOOPBACK_HOST, port: 0 }, -1)).toThrow(/invalid loopback port/);
    expect(() => allocateEndpoint({ host: LOOPBACK_HOST, port: 0 }, 65536)).toThrow(/invalid loopback port/);
  });
});

describe("topology: every missing component is rejected", () => {
  for (const missing of KNOWN_COMPONENTS) {
    it(`rejects a graph missing ${missing}`, () => {
      const topo = validTopology().filter((c) => c.id !== missing);
      const result = validateTopology(candidate(topo));
      expect(result.ok).toBe(false);
      expect(violationCodes(candidate(topo))).toContain("MISSING-COMPONENT");
      expect(result.violations.some((v) => v.component === missing)).toBe(true);
    });
  }

  it("rejects an empty graph", () => {
    const result = validateTopology(candidate([]));
    expect(result.ok).toBe(false);
    expect(violationCodes(candidate([])).filter((c) => c === "MISSING-COMPONENT")).toHaveLength(5);
  });
});

describe("topology: every missing field is rejected", () => {
  const cases: Array<{ name: string; mutate: (topo: ComponentDescriptor[]) => void; code: string }> = [
    {
      name: "executable",
      mutate: (topo) => {
        delete (componentOf(topo, "fastapi") as Partial<ComponentDescriptor>).executable;
      },
      code: "MISSING-EXECUTABLE",
    },
    {
      name: "arguments",
      mutate: (topo) => {
        delete (componentOf(topo, "agent_service") as Partial<ComponentDescriptor>).args;
      },
      code: "MISSING-ARGS",
    },
    {
      name: "endpoint",
      mutate: (topo) => {
        delete (componentOf(topo, "postgres_pgvector") as Partial<ComponentDescriptor>).endpoint;
      },
      code: "MISSING-ENDPOINT",
    },
    {
      name: "dependency list",
      mutate: (topo) => {
        delete (componentOf(topo, "fastapi") as Partial<ComponentDescriptor>).dependsOn;
      },
      code: "MISSING-DEPENDENCIES",
    },
    {
      name: "readiness probe",
      mutate: (topo) => {
        delete (componentOf(topo, "vector_store") as Partial<ComponentDescriptor>).readiness;
      },
      code: "MISSING-PROBE",
    },
    {
      name: "resource root",
      mutate: (topo) => {
        delete (componentOf(topo, "next") as Partial<ComponentDescriptor>).resourceRoot;
      },
      code: "MISSING-RESOURCE-ROOT",
    },
    {
      name: "app-data root",
      mutate: (topo) => {
        delete (componentOf(topo, "next") as Partial<ComponentDescriptor>).appDataRoot;
      },
      code: "MISSING-APPDATA-ROOT",
    },
    {
      name: "log sink",
      mutate: (topo) => {
        delete (componentOf(topo, "fastapi") as Partial<ComponentDescriptor>).logSink;
      },
      code: "MISSING-LOG-SINK",
    },
    {
      name: "shutdown owner",
      mutate: (topo) => {
        delete (componentOf(topo, "postgres_pgvector") as Partial<ComponentDescriptor>).shutdownOwner;
      },
      code: "MISSING-OWNER",
    },
  ];

  for (const { name, mutate, code } of cases) {
    it(`rejects a graph with missing ${name}`, () => {
      const topo = clone(validTopology());
      mutate(topo);
      const result = validateTopology(candidate(topo));
      expect(result.ok).toBe(false);
      expect(violationCodes(candidate(topo))).toContain(code);
    });
  }
});

describe("topology: fixed packaged ports are rejected", () => {
  it("rejects a nonzero endpoint port (fixed packaged port)", () => {
    const topo = clone(validTopology());
    componentOf(topo, "fastapi").endpoint.port = 8010;
    expect(violationCodes(candidate(topo))).toContain("FIXED-PORT");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });

  it("rejects a nonzero probe port (fixed packaged port)", () => {
    const topo = clone(validTopology());
    componentOf(topo, "fastapi").readiness.port = { host: LOOPBACK_HOST, port: 8011 };
    expect(violationCodes(candidate(topo))).toContain("FIXED-PORT");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });
});

describe("topology: non-loopback binds are rejected", () => {
  it("rejects an endpoint bound to 0.0.0.0", () => {
    const topo = clone(validTopology());
    componentOf(topo, "next").endpoint.host = "0.0.0.0";
    expect(violationCodes(candidate(topo))).toContain("NON-LOOPBACK-BIND");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });

  it("rejects a probe bound to a non-loopback host", () => {
    const topo = clone(validTopology());
    componentOf(topo, "vector_store").readiness.port = { host: "0.0.0.0", port: 0 };
    expect(violationCodes(candidate(topo))).toContain("NON-LOOPBACK-BIND");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });
});

describe("topology: writable install paths are rejected", () => {
  it("rejects a log sink inside the resource root", () => {
    const topo = clone(validTopology());
    componentOf(topo, "fastapi").logSink.directory = `${RESOURCE_ROOT}/logs`;
    expect(violationCodes(candidate(topo))).toContain("WRITABLE-INSTALL-PATH");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });

  it("rejects an app-data root inside the resource root", () => {
    const topo = clone(validTopology());
    componentOf(topo, "postgres_pgvector").appDataRoot = `${RESOURCE_ROOT}/pgdata`;
    expect(violationCodes(candidate(topo))).toContain("WRITABLE-INSTALL-PATH");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });

  it("treats a backslash resource-root path the same as a forward-slash one", () => {
    const topo = clone(validTopology());
    componentOf(topo, "fastapi").logSink.directory = `${RESOURCE_ROOT.replaceAll("/", "\\")}\\logs`;
    expect(violationCodes(candidate(topo))).toContain("WRITABLE-INSTALL-PATH");
  });
});

describe("topology: unknown or malformed components are rejected", () => {
  it("rejects an unknown component id", () => {
    const topo = clone(validTopology());
    topo.push({
      id: "electron_shell" as never,
      processType: "child",
      executable: { kind: "bundled-binary", path: `${RESOURCE_ROOT}/evil.exe` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: [],
      readiness: { transport: "tcp" },
      resourceRoot: RESOURCE_ROOT,
      appDataRoot: APP_DATA_ROOT,
      logSink: { directory: `${APP_DATA_ROOT}/logs/evil` },
      shutdownOwner: { kind: "harness" },
    });
    const result = validateTopology(candidate(topo));
    expect(result.ok).toBe(false);
    expect(violationCodes(candidate(topo))).toContain("UNKNOWN-COMPONENT");
  });

  it("rejects a duplicate component id", () => {
    const topo = clone(validTopology());
    topo.push({ ...componentOf(topo, "next") });
    expect(violationCodes(candidate(topo))).toContain("DUPLICATE-COMPONENT");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });

  it("rejects a non-object component entry", () => {
    const topo = [42] as unknown as ComponentDescriptor[];
    expect(violationCodes(candidate(topo))).toContain("NOT-A-DESCRIPTOR");
  });
});

describe("topology: unresolved references are rejected", () => {
  it("rejects a dependency that is not part of the topology", () => {
    const topo = clone(validTopology()).filter((c) => c.id !== "vector_store");
    // vector_store is a known component but is absent from the graph.
    componentOf(topo, "fastapi").dependsOn = ["postgres_pgvector", "vector_store"];
    expect(violationCodes(candidate(topo))).toContain("UNRESOLVED-DEPENDENCY");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });

  it("rejects a dependency on an unknown component name", () => {
    const topo = clone(validTopology());
    componentOf(topo, "fastapi").dependsOn = ["mongo" as never];
    expect(violationCodes(candidate(topo))).toContain("UNKNOWN-DEPENDENCY");
  });

  it("rejects a shutdown owner that is not part of the topology", () => {
    const topo = clone(validTopology()).filter((c) => c.id !== "vector_store");
    // vector_store is a known component but is absent from the graph.
    componentOf(topo, "agent_service").shutdownOwner = { kind: "component", component: "vector_store" };
    expect(violationCodes(candidate(topo))).toContain("UNRESOLVED-OWNER");
    expect(validateTopology(candidate(topo)).ok).toBe(false);
  });

  it("rejects a shutdown owner that is not a known component", () => {
    const topo = clone(validTopology());
    componentOf(topo, "agent_service").shutdownOwner = { kind: "component", component: "gateway" as never };
    expect(violationCodes(candidate(topo))).toContain("UNKNOWN-OWNER");
  });
});

describe("topology: contract mode collapses to one stable failure", () => {
  it("returns a single CONTRACT-REJECTED violation for any unsafe topology", () => {
    const topo = clone(validTopology());
    delete (componentOf(topo, "postgres_pgvector") as Partial<ComponentDescriptor>).shutdownOwner;
    componentOf(topo, "fastapi").endpoint.port = 8010;

    const result = validateTopology(candidate(topo), "contract");
    expect(result.ok).toBe(false);
    expect(result.violations.map((v) => v.code)).toEqual(["CONTRACT-REJECTED"]);
  });

  it("still surfaces unknown components distinctly in contract mode", () => {
    const topo = clone(validTopology());
    topo.push({
      id: "mystery" as never,
      processType: "child",
      executable: { kind: "bundled-binary", path: `${RESOURCE_ROOT}/mystery.exe` },
      args: [],
      endpoint: { host: LOOPBACK_HOST, port: 0 },
      dependsOn: [],
      readiness: { transport: "tcp" },
      resourceRoot: RESOURCE_ROOT,
      appDataRoot: APP_DATA_ROOT,
      logSink: { directory: `${APP_DATA_ROOT}/logs/mystery` },
      shutdownOwner: { kind: "harness" },
    });
    const result = validateTopology(candidate(topo), "contract");
    expect(result.ok).toBe(false);
    expect(violationCodes(candidate(topo))).toContain("UNKNOWN-COMPONENT");
  });
});
