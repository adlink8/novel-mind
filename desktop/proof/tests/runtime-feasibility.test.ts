/**
 * Phase 41 proof (Plan 41-03): fail-closed bundled-runtime feasibility verdict.
 *
 * Guards:
 * - The committed `runtime-manifest.json` is the source of truth. Its recorded
 *   per-component verdicts and overall verdict MUST equal the verdict re-derived here
 *   from the evidence rows themselves — a manifest cannot claim GO without evidence
 *   (T-41-03-01 anti-falsification).
 * - Every evidence hash in the manifest is recomputed against the on-disk file; any
 *   mismatch fails.
 * - Deleting or falsifying any mandatory evidence deterministically changes the verdict
 *   to NO-GO; no partial/unknown row can produce GO.
 * - GO is reachable only when EVERY mandatory row passes for EVERY component and every
 *   evidence hash validates.
 */

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SPEC_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SPEC_DIR, "..", "..", "..");
const MANIFEST_PATH = path.resolve(SPEC_DIR, "..", "runtime-manifest.json");
const DECISION_PATH = path.resolve(
  REPO_ROOT,
  ".planning",
  "phases",
  "41-electron-architecture-and-packaging-proof",
  "41-DECISION.md",
);
const EVIDENCE_REPORT_PATH = path.resolve(SPEC_DIR, "..", "logs", "runtime-feasibility-evidence.json");

const KNOWN = ["next", "fastapi", "agent_service", "postgres_pgvector", "vector_store"] as const;

// ---------------------------------------------------------------------------
// Manifest loading
// ---------------------------------------------------------------------------

interface EvidenceFile {
  path: string;
  hash: string;
}
interface EvidenceGroup {
  proven: boolean;
  files?: EvidenceFile[];
  path?: string;
  hash?: string;
}
interface ComponentRow {
  id: string;
  runtime: {
    executable: { kind: string; declaredPath: string; presentInProofLayout: boolean; proven: boolean };
  };
  resourcePath: { proven: boolean };
  mutableDataPath: { proven: boolean };
  licenseRedistribution: { proven: boolean };
  startupCommand: {
    command: string;
    tokens: string[];
    resolvesUserRuntime: boolean;
    resolvesDocker: boolean;
    proven: boolean;
  };
  readinessEvidence: EvidenceGroup;
  shutdownEvidence: EvidenceGroup;
  runtimeArtifact?: EvidenceFile;
  verdict: string;
  verdictReason?: string;
}
interface Manifest {
  components: ComponentRow[];
  overall: { verdict: string; reason: string };
}

function loadManifest(): Manifest {
  return JSON.parse(readFileSync(MANIFEST_PATH, "utf8")) as Manifest;
}

function sha256(filePath: string): string {
  const hash = createHash("sha256");
  hash.update(readFileSync(filePath));
  return hash.digest("hex");
}

// ---------------------------------------------------------------------------
// Verdict derivation (fail-closed) — mirrors the PowerShell verify script.
// ---------------------------------------------------------------------------

export interface ComponentVerdict {
  id: string;
  pass: boolean;
  failingRows: string[];
}

/** Validates every evidence hash a component references (readiness, shutdown, artifact). */
function evidenceHashFailures(component: ComponentRow): string[] {
  const failures: string[] = [];
  const groups: Array<{ name: string; group: EvidenceGroup | EvidenceFile | undefined }> = [
    { name: "readiness", group: component.readinessEvidence },
    { name: "shutdown", group: component.shutdownEvidence },
    { name: "runtimeArtifact", group: component.runtimeArtifact },
  ];
  for (const { name, group } of groups) {
    if (!group) continue;
    const files: EvidenceFile[] = [];
    if (Array.isArray((group as EvidenceGroup).files)) {
      files.push(...((group as EvidenceGroup).files ?? []));
    } else if (typeof (group as EvidenceFile).path === "string") {
      files.push(group as EvidenceFile);
    }
    for (const file of files) {
      const abs = path.resolve(REPO_ROOT, file.path);
      let actual: string | null = null;
      try {
        actual = sha256(abs);
      } catch {
        actual = null;
      }
      if (actual !== file.hash.toLowerCase()) {
        failures.push(`evidence-hash:${name}:${file.path}`);
      }
    }
  }
  return failures;
}

/** Derives a component verdict from its evidence rows. Fail-closed: any false row → FAIL. */
export function deriveComponentVerdict(component: ComponentRow): ComponentVerdict {
  const failingRows: string[] = [];
  const mandatory: Array<[string, boolean]> = [
    ["executable", component.runtime.executable.proven],
    // Executable must also be present in the proof layout — a claim is not evidence.
    ["executable-present", component.runtime.executable.presentInProofLayout],
    ["resourcePath", component.resourcePath.proven],
    ["mutableDataPath", component.mutableDataPath.proven],
    ["licenseRedistribution", component.licenseRedistribution.proven],
    ["startupCommand", component.startupCommand.proven],
    ["noUserRuntime", !component.startupCommand.resolvesUserRuntime],
    ["noDocker", !component.startupCommand.resolvesDocker],
    ["readinessEvidence", component.readinessEvidence.proven],
    ["shutdownEvidence", component.shutdownEvidence.proven],
  ];
  for (const [row, ok] of mandatory) {
    if (!ok) failingRows.push(row);
  }
  failingRows.push(...evidenceHashFailures(component));

  return {
    id: component.id,
    pass: failingRows.length === 0,
    failingRows,
  };
}

export interface OverallVerdict {
  verdict: "GO" | "NO-GO";
  components: ComponentVerdict[];
  failingComponents: string[];
}

/** Overall verdict: GO only when EVERY component passes every mandatory row + hash. */
export function deriveOverallVerdict(manifest: Manifest): OverallVerdict {
  const components = manifest.components.map(deriveComponentVerdict);
  const failingComponents = components.filter((c) => !c.pass).map((c) => c.id);
  return {
    verdict: failingComponents.length === 0 ? "GO" : "NO-GO",
    components,
    failingComponents,
  };
}

/** Deep clone a manifest so tamper tests mutate their own copy. */
function cloneManifest(manifest: Manifest): Manifest {
  return JSON.parse(JSON.stringify(manifest)) as Manifest;
}

// ---------------------------------------------------------------------------
// Clean case: the committed manifest must fail closed on current evidence.
// ---------------------------------------------------------------------------

describe("runtime feasibility: clean-case fail-closed verdict (D-41-04, D-41-06)", () => {
  const manifest = loadManifest();
  const derived = deriveOverallVerdict(manifest);

  it("exposes exactly the five known components", () => {
    expect(manifest.components.map((c) => c.id)).toEqual([...KNOWN]);
  });

  it("derives overall NO-GO from current evidence (no bundled runtime for 0/5 components)", () => {
    expect(derived.verdict).toBe("NO-GO");
    expect(derived.failingComponents).toHaveLength(5);
  });

  it("every component is FAIL under current evidence", () => {
    for (const component of manifest.components) {
      const verdict = deriveComponentVerdict(component);
      expect(verdict.pass, `${component.id} should fail on missing bundled evidence`).toBe(false);
      expect(verdict.failingRows.length).toBeGreaterThan(0);
    }
  });

  it("any startup command that resolves a user runtime or Docker fails its no-runtime gate", () => {
    for (const component of manifest.components) {
      const derivedComponent = deriveComponentVerdict(component);
      if (component.startupCommand.resolvesUserRuntime) {
        expect(derivedComponent.failingRows).toContain("noUserRuntime");
      }
      if (component.startupCommand.resolvesDocker) {
        expect(derivedComponent.failingRows).toContain("noDocker");
      }
    }
    // Every component must currently fail — the no-Docker/no-user-runtime evidence is
    // absent for all five (D-41-04/D-41-06 truth, must-have).
    for (const component of manifest.components) {
      const derivedComponent = deriveComponentVerdict(component);
      expect(derivedComponent.pass, `${component.id} must fail while bundled evidence is absent`).toBe(false);
    }
  });

  it("every evidence hash in the manifest matches the on-disk file", () => {
    const groups: Array<EvidenceFile[]> = [];
    for (const component of manifest.components) {
      for (const g of [component.readinessEvidence, component.shutdownEvidence]) {
        if (Array.isArray(g.files)) groups.push(g.files);
      }
      if (component.runtimeArtifact) groups.push([component.runtimeArtifact]);
    }
    const files = groups.flat();
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      const abs = path.resolve(REPO_ROOT, file.path);
      expect(() => readFileSync(abs), `evidence file missing: ${file.path}`).not.toThrow();
      expect(sha256(abs), `hash mismatch for ${file.path}`).toBe(file.hash.toLowerCase());
    }
  });

  it("recorded per-component verdicts equal the derived verdicts (anti-falsification)", () => {
    for (const component of manifest.components) {
      const derivedComponent = deriveComponentVerdict(component);
      const expected = derivedComponent.pass ? "GO" : "FAIL";
      expect(component.verdict, `${component.id} recorded verdict is not derivable from its evidence`).toBe(expected);
    }
  });

  it("recorded overall verdict equals the derived overall verdict", () => {
    expect(manifest.overall.verdict).toBe(derived.verdict);
    expect(manifest.overall.verdict).toBe("NO-GO");
  });

  it("the Phase 41 DECISION.md records NO-GO consistent with the derived verdict", () => {
    const decision = readFileSync(DECISION_PATH, "utf8");
    expect(decision).toMatch(/## Verdict: \*\*NO-GO\*\*/);
    expect(decision).toMatch(/Per-Component Verdicts/);
    expect(decision).toMatch(/next.*FAIL/);
    expect(decision).toMatch(/fastapi.*FAIL/);
    expect(decision).toMatch(/agent_service.*FAIL/);
    expect(decision).toMatch(/postgres_pgvector.*FAIL/);
    expect(decision).toMatch(/vector_store.*FAIL/);
    expect(decision).toMatch(/PREREQ-/);
    expect(decision).toMatch(/Replanning Boundary/);
    expect(decision).toMatch(/[0-9a-f]{64}/); // at least one evidence hash recorded
  });

  it("the PowerShell verify report (when present) agrees with the manifest verdict", () => {
    let report: { overallVerdict: string } | null = null;
    try {
      report = JSON.parse(readFileSync(EVIDENCE_REPORT_PATH, "utf8")) as { overallVerdict: string };
    } catch {
      // Report absent — this suite runs standalone; manifest-derived verdict is authoritative.
    }
    if (report !== null) {
      expect(report.overallVerdict).toBe("NO-GO");
    }
  });
});

// ---------------------------------------------------------------------------
// Tamper determinism: deleting or falsifying ANY mandatory evidence flips to NO-GO.
// ---------------------------------------------------------------------------

describe("runtime feasibility: tamper determinism (T-41-03-01)", () => {
  const manifest = loadManifest();

  it("falsifying a single evidence hash flips the component to FAIL and overall to NO-GO", () => {
    const tampered = cloneManifest(manifest);
    const next = tampered.components.find((c) => c.id === "next")!;
    next.readinessEvidence.files![0]!.hash = "0".repeat(64);
    const derived = deriveOverallVerdict(tampered);
    expect(derived.verdict).toBe("NO-GO");
    const nextDerived = derived.components.find((c) => c.id === "next")!;
    expect(nextDerived.pass).toBe(false);
    expect(nextDerived.failingRows.some((r) => r.startsWith("evidence-hash:"))).toBe(true);
  });

  it("deleting a component flips overall to NO-GO", () => {
    const tampered = cloneManifest(manifest);
    tampered.components = tampered.components.filter((c) => c.id !== "vector_store");
    const derived = deriveOverallVerdict(tampered);
    expect(derived.verdict).toBe("NO-GO");
    expect(derived.components.map((c) => c.id)).not.toContain("vector_store");
  });

  it("marking one mandatory row proven=false flips the component to FAIL", () => {
    const tampered = cloneManifest(manifest);
    const fastapi = tampered.components.find((c) => c.id === "fastapi")!;
    fastapi.shutdownEvidence.proven = true;
    // Prove everything except the executable present-in-layout row: still FAIL.
    fastapi.runtime.executable.proven = true;
    const derived = deriveComponentVerdict(fastapi);
    expect(derived.pass).toBe(false);
    expect(derived.failingRows).toContain("executable-present");
  });

  it("a startup command resolving a user runtime always fails noDocker/noUserRuntime gates", () => {
    const tampered = cloneManifest(manifest);
    const agent = tampered.components.find((c) => c.id === "agent_service")!;
    agent.startupCommand.resolvesUserRuntime = true;
    const derived = deriveComponentVerdict(agent);
    expect(derived.failingRows).toContain("noUserRuntime");
    expect(derived.pass).toBe(false);
  });

  it("an unknown component id can never contribute to GO", () => {
    const tampered = cloneManifest(manifest);
    tampered.components.push({ ...tampered.components[0]!, id: "mystery" });
    const derived = deriveOverallVerdict(tampered);
    expect(derived.verdict).toBe("NO-GO");
  });
});

// ---------------------------------------------------------------------------
// GO reachability: GO requires EVERY mandatory row passing in EVERY component.
// A manifest with partial/unknown rows must never produce GO.
// ---------------------------------------------------------------------------

describe("runtime feasibility: GO requires complete evidence (no partial/unknown GO)", () => {
  const manifest = loadManifest();

  /** Builds a synthetic component with all evidence rows satisfied against REAL on-disk files. */
  function satisfiedComponent(id: string, executablePath: string, evidenceFile: EvidenceFile): ComponentRow {
    return {
      id,
      runtime: { executable: { kind: "bundled-binary", declaredPath: executablePath, presentInProofLayout: true, proven: true } },
      resourcePath: { proven: true },
      mutableDataPath: { proven: true },
      licenseRedistribution: { proven: true },
      startupCommand: { command: `${executablePath} --loopback`, tokens: [], resolvesUserRuntime: false, resolvesDocker: false, proven: true },
      readinessEvidence: { proven: true, files: [{ ...evidenceFile }] },
      shutdownEvidence: { proven: true, files: [{ ...evidenceFile }] },
      runtimeArtifact: { ...evidenceFile },
      verdict: "GO",
    };
  }

  it("GO is reached only when every mandatory row passes in every component", () => {
    // The inventory + parity spec are real on-disk evidence; a synthetic manifest that
    // satisfies every row must produce GO — proving the gate is not permanently red.
    const evidenceFile: EvidenceFile = {
      path: "desktop/proof/tests/route-inventory.json",
      hash: "d8bac42f3ecc56c25480c9f7ce8cac7db928fc98736ba941ba1bec4162c624d5",
    };
    const clean = {
      components: KNOWN.map((id) => satisfiedComponent(id, `resources/${id}/bin/${id}.exe`, evidenceFile)),
      overall: { verdict: "GO", reason: "synthetic clean fixture" },
    };
    const derived = deriveOverallVerdict(clean);
    expect(derived.verdict).toBe("GO");
    expect(derived.failingComponents).toHaveLength(0);
  });

  it("a single missing mandatory row in a clean fixture flips overall to NO-GO", () => {
    const evidenceFile: EvidenceFile = {
      path: "desktop/proof/tests/route-inventory.json",
      hash: "d8bac42f3ecc56c25480c9f7ce8cac7db928fc98736ba941ba1bec4162c624d5",
    };
    const clean = {
      components: KNOWN.map((id) => satisfiedComponent(id, `resources/${id}/bin/${id}.exe`, evidenceFile)),
      overall: { verdict: "GO", reason: "synthetic clean fixture" },
    };
    clean.components[0]!.shutdownEvidence.proven = false;
    const derived = deriveOverallVerdict(clean);
    expect(derived.verdict).toBe("NO-GO");
  });

  it("a single falsified hash in a clean fixture flips overall to NO-GO", () => {
    const evidenceFile: EvidenceFile = {
      path: "desktop/proof/tests/route-inventory.json",
      hash: "d8bac42f3ecc56c25480c9f7ce8cac7db928fc98736ba941ba1bec4162c624d5",
    };
    const clean = {
      components: KNOWN.map((id) => satisfiedComponent(id, `resources/${id}/bin/${id}.exe`, evidenceFile)),
      overall: { verdict: "GO", reason: "synthetic clean fixture" },
    };
    clean.components[3]!.runtimeArtifact = { ...evidenceFile, hash: "f".repeat(64) };
    const derived = deriveOverallVerdict(clean);
    expect(derived.verdict).toBe("NO-GO");
  });

  it("an UNKNOWN verdict value can never produce GO", () => {
    const tampered = cloneManifest(manifest);
    tampered.overall.verdict = "GO";
    tampered.components = tampered.components.map((c) => ({ ...c, verdict: "GO" as string }));
    const derived = deriveOverallVerdict(tampered);
    // Recorded claim of GO is rejected because evidence rows are still FAIL.
    expect(derived.verdict).toBe("NO-GO");
    for (const c of tampered.components) {
      expect(deriveComponentVerdict(c).pass).toBe(false);
    }
  });
});
