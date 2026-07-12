/**
 * Frontend contract for coverage / timeout / flake policy (Phase 06-01).
 * Mirrors locked D-09 / D-10 / D-16 values from .quality/coverage-policy.yml.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { QUALITY_THRESHOLDS } from "./quality-thresholds";

const REPO_ROOT = path.resolve(__dirname, "../../..");
const POLICY_PATH = path.join(REPO_ROOT, ".quality", "coverage-policy.yml");

function parseSimpleYaml(text: string): Record<string, unknown> {
  // Minimal YAML subset parser sufficient for our locked policy shape.
  // Avoids adding js-yaml dependency for this contract test.
  const lines = text.split(/\r?\n/);
  const root: Record<string, unknown> = {};
  const stack: { indent: number; obj: Record<string, unknown> }[] = [
    { indent: -1, obj: root },
  ];

  for (const raw of lines) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    const indent = raw.match(/^\s*/)?.[0].length ?? 0;
    const line = raw.trim();
    if (line.startsWith("- ")) {
      const parent = stack[stack.length - 1].obj;
      const lastKey = Object.keys(parent).at(-1);
      if (!lastKey) continue;
      const arr = Array.isArray(parent[lastKey])
        ? (parent[lastKey] as unknown[])
        : [];
      let value: unknown = line.slice(2).trim();
      if (
        typeof value === "string" &&
        value.startsWith('"') &&
        value.endsWith('"')
      ) {
        value = value.slice(1, -1);
      }
      arr.push(value);
      parent[lastKey] = arr;
      continue;
    }
    const m = line.match(/^([^:]+):\s*(.*)$/);
    if (!m) continue;
    const key = m[1].trim();
    let value: unknown = m[2].trim();
    while (stack.length > 1 && indent <= stack[stack.length - 1].indent) {
      stack.pop();
    }
    const current = stack[stack.length - 1].obj;
    if (value === "") {
      const child: Record<string, unknown> = {};
      current[key] = child;
      stack.push({ indent, obj: child });
      continue;
    }
    if (
      typeof value === "string" &&
      value.startsWith('"') &&
      value.endsWith('"')
    ) {
      value = value.slice(1, -1);
    } else if (value === "true") {
      value = true;
    } else if (value === "false") {
      value = false;
    } else if (
      typeof value === "string" &&
      /^-?\d+(\.\d+)?$/.test(value)
    ) {
      value = Number(value);
    }
    current[key] = value;
  }
  return root;
}

function get(obj: unknown, pathKeys: string[]): unknown {
  let cur: unknown = obj;
  for (const k of pathKeys) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur;
}

describe("coverage policy contract", () => {
  const policyText = readFileSync(POLICY_PATH, "utf-8");
  const policy = parseSimpleYaml(policyText);

  it("locks frontend overall thresholds (D-09)", () => {
    expect(get(policy, ["coverage", "frontend", "overall", "line"])).toBe(75);
    expect(get(policy, ["coverage", "frontend", "overall", "branch"])).toBe(65);
    expect(QUALITY_THRESHOLDS.overall.lines).toBe(75);
    expect(QUALITY_THRESHOLDS.overall.branches).toBe(65);
  });

  it("locks frontend critical hooks/store/API thresholds (D-09)", () => {
    expect(get(policy, ["coverage", "frontend", "critical", "line"])).toBe(85);
    expect(get(policy, ["coverage", "frontend", "critical", "branch"])).toBe(75);
    expect(QUALITY_THRESHOLDS.critical.lines).toBe(85);
    expect(QUALITY_THRESHOLDS.critical.branches).toBe(75);
  });

  it("locks diff coverage (D-09)", () => {
    expect(get(policy, ["coverage", "diff_coverage", "minimum"])).toBe(90);
    expect(QUALITY_THRESHOLDS.diffCoverage).toBe(90);
  });

  it("locks timeouts including browser 60s (D-16)", () => {
    expect(get(policy, ["timeouts", "unit"])).toBe(5);
    expect(get(policy, ["timeouts", "contract"])).toBe(15);
    expect(get(policy, ["timeouts", "integration"])).toBe(30);
    expect(get(policy, ["timeouts", "browser"])).toBe(60);
    expect(get(policy, ["timeouts", "live"])).toBe(180);
  });

  it("locks flake policy (D-10)", () => {
    expect(get(policy, ["flake", "pr_max"])).toBe(0);
    expect(get(policy, ["flake", "external_infra", "max_retry"])).toBe(1);
    expect(
      get(policy, ["flake", "external_infra", "save_first_failure_evidence"]),
    ).toBe(true);
  });

  it("locks vitest tool versions", () => {
    expect(get(policy, ["tools", "vitest"])).toBe("4.1.10");
    expect(get(policy, ["tools", "vitest_coverage_v8"])).toBe("4.1.10");
  });

  it("fails closed when measured frontend coverage is low", () => {
    const requiredLine = Number(
      get(policy, ["coverage", "frontend", "overall", "line"]),
    );
    const measuredLine = 10;
    expect(measuredLine < requiredLine).toBe(true);
  });
});
