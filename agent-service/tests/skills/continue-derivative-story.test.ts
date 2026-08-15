import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  loadSkill,
  loadAllowlistedSkills,
} from "../../src/skills/loader.js";

/**
 * continue-derivative-story skill 契约测试（Phase 37-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 37 9 工具 allowlist
 * （7 只读 + allow_divergence / publish_derivative_revision 两个 action）、真实
 * loader 接受 pinned manifest（fail-closed 通过后才存在 LoadedSkill）、
 * registry-valid fail-closed（未知工具/未声明权限/schema drift）、双 fixture 通过
 * schema、schema-mismatch 负面用例、BranchSuggestion 六字段 + enabled_by_default
 * =false（candidate-only，不自动 fork、不复用 divergence approval）、Phase 37
 * 边界（candidate-only DraftArtifact/ContinuityReport → allow_divergence approval
 * → revalidation → 独立 publish approval → deterministic publisher；Agent 绝不
 * 直接写 Original Canon / 域表 / published 状态）、26-06 normalization trail。
 * 不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/continue-derivative-story/", import.meta.url);

// 返回 any：JSON.parse 的产物是动态 JSON；`as SkillManifest` / `Value.Check`
// 需要宽松类型（25.2-05 tsc 门禁修复，不改断言语义）。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 37-05 注册的 20 个域工具（域工具全集；36-05 18 个 + Phase 37
 *  allow_divergence / publish_derivative_revision 两个 action）。 */
const REGISTERED_DOMAIN_TOOLS = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "get_timeline",
  "get_relationships",
  "get_clues",
  "get_narrative_memory",
  "get_events",
  "get_character_state",
  "get_character_knowledge",
  "get_world_rules",
  "get_evidence_span",
  "get_visual_bible",
  "generate_image_candidate",
  "publish_illustration",
  "attach_illustration_to_text",
  "create_canon_fork",
  "apply_derivative_edit",
  "allow_divergence",
  "publish_derivative_revision",
] as const;

/** Phase 37 编排 allowlist：7 个只读域工具 + 2 个 action 工具。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "get_timeline",
  "get_relationships",
  "get_clues",
  "get_narrative_memory",
  "allow_divergence",
  "publish_derivative_revision",
] as const;

/** Phase 37 声明的审批动作集合：两个 action 各自要求独立 Web ApprovalRequest
 * （D-11/D-15）；divergence approval 与 publish approval 绑定相同 hash，绝不复用。 */
const DECLARED_APPROVAL_ACTIONS: readonly string[] = [
  "allow_divergence",
  "publish_derivative_revision",
];

/** BranchSuggestion 六字段契约（D-37-05 / REQ-FORK-06）。 */
const BRANCH_SUGGESTION_FIELDS = [
  "choice_text",
  "branch_summary",
  "triggering_conflict",
  "canon_delta_hash",
  "evidence_refs",
  "enabled_by_default",
] as const;

/** D-09 必需字段集。 */
const D09_FIELDS = [
  "name",
  "version",
  "allowed_tools",
  "read_permissions",
  "write_permissions",
  "forbidden_spaces",
  "budget",
  "approval_required_for",
  "input_schema",
  "output_schema",
] as const;

/**
 * 支持本项目 skill.yaml 语法子集的极简 YAML 解析器（顶层标量 / `[]` / `>-` block
 * scalar / 缩进 list / 缩进 dict，去注释、数值与引号归一）。
 */
function parseSkillYaml(text: string): Record<string, unknown> {
  const lines = text.split(/\r?\n/);
  const out: Record<string, unknown> = {};
  let i = 0;
  const isComment = (s: string): boolean => s.trim().startsWith("#");
  const toScalar = (raw: string): unknown => {
    let v = raw.replace(/\s*#.*$/, "").trim();
    if (v === "[]") return [];
    if (/^-?\d+$/.test(v)) return Number(v);
    if (
      (v.startsWith('"') && v.endsWith('"')) ||
      (v.startsWith("'") && v.endsWith("'"))
    ) {
      return v.slice(1, -1);
    }
    return v;
  };

  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "" || isComment(line)) {
      i++;
      continue;
    }
    const top = line.match(/^([A-Za-z0-9_.-]+):(?:\s*(.*))?$/);
    if (!top) {
      i++;
      continue;
    }
    const key = top[1];
    const raw = (top[2] ?? "").trim();

    if (raw === ">-") {
      const parts: string[] = [];
      i++;
      while (i < lines.length) {
        const next = lines[i];
        if (next.trim() === "" || isComment(next)) {
          i++;
          continue;
        }
        if (!/^\s/.test(next)) break;
        const piece = next.trim().replace(/\s*#.*$/, "").trim();
        if (piece !== "") parts.push(piece);
        i++;
      }
      out[key] = parts.join(" ").replace(/\s+/g, " ").trim();
      continue;
    }

    if (raw === "") {
      i++;
      if (i < lines.length && /^\s+- /.test(lines[i])) {
        const items: string[] = [];
        while (i < lines.length) {
          const next = lines[i];
          if (next.trim() === "" || isComment(next)) {
            i++;
            continue;
          }
          if (!/^\s+- /.test(next)) break;
          items.push(next.trim().slice(2).trim());
          i++;
        }
        out[key] = items;
      } else {
        const sub: Record<string, unknown> = {};
        while (i < lines.length) {
          const next = lines[i];
          if (next.trim() === "" || isComment(next)) {
            i++;
            continue;
          }
          if (!/^\s/.test(next)) break;
          const m = next.match(/^\s+([A-Za-z0-9_.-]+):(?:\s*(.*))?$/);
          if (!m) break;
          sub[m[1]] = toScalar(m[2] ?? "");
          i++;
        }
        out[key] = sub;
      }
      continue;
    }

    out[key] = toScalar(raw);
    i++;
  }
  return out;
}

interface SkillManifest {
  name: string;
  version: string;
  allowed_tools: string[];
  read_permissions: string[];
  write_permissions: string[];
  forbidden_spaces: string[];
  budget: Record<string, unknown>;
  approval_required_for: string[];
  input_schema: string;
  output_schema: string;
}

/**
 * Skill-local registry-valid 校验（模拟 25.2-05 loader 的 fail-closed 门禁语义）：
 * allowed_tools ⊆ 注册工具集；write_permissions 为空（Agent 零域写入）；
 * approval_required_for 只允许 Phase 37 声明的 action（allow_divergence /
 * publish_derivative_revision，各自要求独立 Web ApprovalRequest）。返回错误列表。
 */
function validateSkillContract(m: SkillManifest): string[] {
  const errors: string[] = [];
  const registered = new Set<string>(REGISTERED_DOMAIN_TOOLS);
  const unknown = m.allowed_tools.filter((t) => !registered.has(t));
  if (unknown.length > 0) {
    errors.push(`unknown tools: ${unknown.join(", ")}`);
  }
  if (m.allowed_tools.length === 0) {
    errors.push("allowed_tools must be non-empty");
  }
  if (m.write_permissions.length > 0) {
    errors.push("agent must declare empty write_permissions");
  }
  const undeclared = m.approval_required_for.filter(
    (action) => !DECLARED_APPROVAL_ACTIONS.includes(action),
  );
  if (undeclared.length > 0) {
    errors.push(`undeclared approval actions: ${undeclared.join(", ")}`);
  }
  return errors;
}

describe("continue-derivative-story skill package", () => {
  describe("真实 loader 接受 pinned manifest（37-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("continue-derivative-story");
      expect(skill.name).toBe("continue-derivative-story");
      expect(skill.version).toBe("1.0.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor).toEqual([
        "allow_divergence",
        "publish_derivative_revision",
      ]);
      expect(skill.forbiddenSpaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "user_interpretation",
          "derivative:autosave",
          "derivative:direct_write",
          "derivative_generation:write",
          "approval_request",
          "revision_service",
        ]),
      );
      expect(skill.readPermissions).toEqual(
        expect.arrayContaining(["canon", "fanfiction_canon"]),
      );
      expect(skill.instructions.length).toBeGreaterThan(0);
      expect(typeof skill.validateInput).toBe("function");
      expect(typeof skill.validateOutput).toBe("function");
    });

    it("pinned manifest 的 input 通过真实 loader 编译的校验器", () => {
      const skill = loadSkill("continue-derivative-story");
      const example = readSkillJson("examples/basic.json") as { input: any };
      expect(skill.validateInput(example.input)).toBe(true);
    });

    it("loadAllowlistedSkills 包含 continue-derivative-story（ResourceLoader allowlist 注册）", () => {
      const names = loadAllowlistedSkills().map((s) => s.name);
      expect(names).toContain("continue-derivative-story");
    });
  });

  describe("JSON Schema 文件有效性", () => {
    it("input.schema.json 是合法 JSON 且为 draft-07 对象 schema", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
      expect(schema.type).toBe("object");
      expect(Array.isArray(schema.required)).toBe(true);
      for (const field of [
        "novel_id",
        "project_id",
        "chapter_id",
        "chapter_number",
        "intent",
        "context_package_id",
        "evidence_refs",
        "requested_actions",
      ]) {
        expect(schema.required).toContain(field);
      }
      expect(schema.additionalProperties).toBe(false);
    });

    it("output.schema.json 是合法 JSON 且为 draft-07 对象 schema", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
      expect(schema.type).toBe("object");
      expect(schema.required).toContain("draft");
      expect(schema.required).toContain("continuity_report");
    });

    it("output.schema.json 物化 DraftArtifact 信封（D-37-02/D-37-05）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("derivative_draft");
      expect(schema.properties.schema_version.const).toBe("draft-artifact.v1");
      for (const field of [
        "evidence_refs",
        "input_hash",
        "model_lineage",
        "source_versions",
        "producing_skill",
        "producing_skill_version",
        "skill_version_id",
        "draft",
        "continuity_report",
        "branch_suggestions",
        "tool_runs",
        "normalization",
        "status",
        "parent_revision",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.status.enum).toEqual([
        "candidate",
        "validated",
        "approved",
        "published",
        "rejected",
      ]);
    });

    it("draft 负载声明完整 branch-aware 血缘（authority_space 恒为 derivative）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const draft = schema.properties.draft;
      expect(draft.required).toEqual(
        expect.arrayContaining([
          "schema_version",
          "artifact_kind",
          "authority_space",
          "intent",
          "draft_text",
          "source_snapshot_id",
          "source_snapshot_hash",
          "package_hash",
          "manifest_hash",
          "draft_hash",
        ]),
      );
      expect(draft.properties.schema_version.const).toBe("derivative-candidate.v1");
      expect(draft.properties.artifact_kind.const).toBe("derivative_draft");
      expect(draft.properties.authority_space.const).toBe("derivative");
      expect(draft.properties.intent.enum).toEqual(["continuation", "rewrite"]);
      expect(draft.properties.draft_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(draft.properties.canon_delta_hash.pattern).toBe("^[0-9a-f]{64}$");
    });

    it("BranchSuggestion 六字段 + enabled_by_default 恒为 false（D-37-05）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const suggestion = schema.definitions.branch_suggestion;
      for (const field of BRANCH_SUGGESTION_FIELDS) {
        expect(suggestion.required).toContain(field);
      }
      expect(suggestion.properties.enabled_by_default.const).toBe(false);
      expect(suggestion.properties.canon_delta_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(suggestion.additionalProperties).toBe(false);
    });

    it("continuity_report 声明确定性 gate 快照（verdict 三值）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const report = schema.properties.continuity_report;
      expect(report.required).toContain("verdict");
      expect(report.properties.verdict.enum).toEqual([
        "candidate",
        "blocked",
        "needs_override",
      ]);
    });

    it("input.schema.json 声明 Phase 37 输入锚（确定性生成域按引用消费）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of [
        "novel_id",
        "branch",
        "fork",
        "project_id",
        "chapter_id",
        "chapter_number",
        "intent",
        "context_package_id",
        "source_snapshot_id",
        "source_snapshot_hash",
        "evidence_refs",
        "requested_actions",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.intent.enum).toEqual(["continuation", "rewrite"]);
      expect(schema.properties.source_snapshot_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(schema.properties.requested_actions.items.enum).toEqual([
        "allow_divergence",
        "publish_derivative_revision",
      ]);
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("continue-derivative-story");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 9 个 Phase 37 域工具（7 只读 + 2 action）", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      // 全部是注册域工具（loader 会校验 ⊆ DOMAIN_TOOL_NAMES）。
      for (const tool of manifest.allowed_tools) {
        expect(REGISTERED_DOMAIN_TOOLS).toContain(tool);
      }
    });

    it("allowed_tools 含两个 action 工具（且不含任何 action 越界）", () => {
      expect(manifest.allowed_tools).toContain("allow_divergence");
      expect(manifest.allowed_tools).toContain("publish_derivative_revision");
      expect(manifest.allowed_tools).not.toContain("apply_derivative_edit");
      expect(manifest.allowed_tools).not.toContain("create_canon_fork");
      expect(manifest.allowed_tools).not.toContain("publish_illustration");
      expect(manifest.allowed_tools).not.toContain("attach_illustration_to_text");
      expect(manifest.allowed_tools).not.toContain("generate_image_candidate");
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 恰为两个 action（各自独立 Web ApprovalRequest）", () => {
      expect(manifest.approval_required_for.sort()).toEqual([
        "allow_divergence",
        "publish_derivative_revision",
      ]);
    });

    it("forbidden_spaces 覆盖 canon:original、user_interpretation、derivative:autosave/direct_write、derivative_generation:write、approval_request、revision_service", () => {
      expect(manifest.forbidden_spaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "user_interpretation",
          "derivative:autosave",
          "derivative:direct_write",
          "derivative_generation:write",
          "approval_request",
          "revision_service",
        ]),
      );
    });

    it("read_permissions 声明 canon/fanfiction_canon 只读", () => {
      expect(manifest.read_permissions).toEqual(
        expect.arrayContaining(["canon", "fanfiction_canon"]),
      );
    });

    it("budget 声明 per-run 上限", () => {
      expect(manifest.budget.max_calls).toBe(40);
      expect(manifest.budget.max_input_tokens).toBe(40000);
      expect(manifest.budget.max_output_tokens).toBe(12000);
      expect(String(manifest.budget.max_cost_usd)).toBe("4.00");
    });

    it("schema 指向同级文件", () => {
      expect(manifest.input_schema).toBe("./input.schema.json");
      expect(manifest.output_schema).toBe("./output.schema.json");
    });
  });

  describe("registry-valid 契约（fail closed）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("合法：真实 skill.yaml 通过校验", () => {
      expect(validateSkillContract(manifest)).toEqual([]);
    });

    it("拒绝白名单外的未声明工具", () => {
      const errors = validateSkillContract({
        ...manifest,
        allowed_tools: [...manifest.allowed_tools, "publish_derivative_directly"],
      });
      expect(errors).toContain("unknown tools: publish_derivative_directly");
    });

    it("拒绝空 allowed_tools", () => {
      const errors = validateSkillContract({ ...manifest, allowed_tools: [] });
      expect(errors).toContain("allowed_tools must be non-empty");
    });

    it("拒绝非空 write_permissions（Agent 零域写入）", () => {
      const errors = validateSkillContract({
        ...manifest,
        write_permissions: ["derivative_generation:write"],
      });
      expect(errors).toContain("agent must declare empty write_permissions");
    });

    it("拒绝未声明的审批动作（agent 不能自行声明 approval action）", () => {
      const errors = validateSkillContract({
        ...manifest,
        approval_required_for: ["revision_service"],
      });
      expect(errors).toContain(
        "undeclared approval actions: revision_service",
      );
    });

    it("schema drift：output type 是声明的 derivative_draft（真实 loader 编译通过）", () => {
      expect(() => loadSkill("continue-derivative-story")).not.toThrow();
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("derivative_draft");
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, example.input)).toBe(true);
    });

    it("examples/basic.json expected_output（DraftArtifact）通过 output.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("tests/basic.json input 通过 input.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, fixture.input)).toBe(true);
    });

    it("tests/basic.json expected_output（candidate DraftArtifact）通过 output.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, fixture.expected_output)).toBe(true);
    });
  });

  describe("schema-mismatch 负面用例", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");
    const example = readSkillJson("examples/basic.json") as {
      input: any;
      expected_output: any;
    };

    it("input 缺 novel_id → 拒绝", () => {
      const { novel_id, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 缺 project_id → 拒绝（derivative project 必须）", () => {
      const { project_id, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 缺 context_package_id → 拒绝（冻结 package 血缘必须）", () => {
      const { context_package_id, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 的 intent 非法 → 拒绝", () => {
      expect(
        Value.Check(inputSchema, { ...example.input, intent: "invent" }),
      ).toBe(false);
    });

    it("input 的 requested_actions 含未知 action → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          requested_actions: ["apply_derivative_edit_directly"],
        }),
      ).toBe(false);
    });

    it("input 的 evidence_refs 为空数组 → 拒绝（leaf-evidence 资格门）", () => {
      expect(
        Value.Check(inputSchema, { ...example.input, evidence_refs: [] }),
      ).toBe(false);
    });

    it("input 的 source_snapshot_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          source_snapshot_hash: "not-a-hash",
        }),
      ).toBe(false);
    });

    it("input 多余未知字段（additionalProperties:false）→ 拒绝", () => {
      expect(
        Value.Check(inputSchema, { ...example.input, hacked: true }),
      ).toBe(false);
    });

    it("output 的 type 非法（既非 derivative_draft）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          type: "story_arc",
        }),
      ).toBe(false);
    });

    it("output 的 schema_version 非法（schema drift）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          schema_version: "draft-artifact.v2",
        }),
      ).toBe(false);
    });

    it("output 的 status 非法 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          status: "bogus",
        }),
      ).toBe(false);
    });

    it("output 的 evidence_refs 为空数组 → 拒绝（leaf-evidence 资格门）", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          evidence_refs: [],
        }),
      ).toBe(false);
    });

    it("output 的 input_hash 非法（非 64 位 hex）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          input_hash: "not-a-hash",
        }),
      ).toBe(false);
    });

    it("output 缺 draft 负载 → 拒绝", () => {
      const { draft, ...broken } = example.expected_output;
      expect(Value.Check(outputSchema, { ...broken })).toBe(false);
    });

    it("output 缺 continuity_report → 拒绝", () => {
      const { continuity_report, ...broken } = example.expected_output;
      expect(Value.Check(outputSchema, { ...broken })).toBe(false);
    });

    it("output 的 draft.authority_space 非法 → 拒绝（derivative-only）", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          draft: { ...example.expected_output.draft, authority_space: "original" },
        }),
      ).toBe(false);
    });

    it("output 的 draft.draft_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          draft: { ...example.expected_output.draft, draft_hash: "not-a-hash" },
        }),
      ).toBe(false);
    });

    it("output 的 continuity_report.verdict 非法 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          continuity_report: {
            ...example.expected_output.continuity_report,
            verdict: "published",
          },
        }),
      ).toBe(false);
    });

    it("output 的 BranchSuggestion 缺 triggering_conflict → 拒绝（六字段契约）", () => {
      const suggestion = {
        ...example.expected_output.branch_suggestions[0],
      };
      const { triggering_conflict, ...broken } = suggestion;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          branch_suggestions: [{ ...broken }],
        }),
      ).toBe(false);
    });

    it("output 的 BranchSuggestion enabled_by_default=true → 拒绝（默认禁用）", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          branch_suggestions: [
            { ...example.expected_output.branch_suggestions[0], enabled_by_default: true },
          ],
        }),
      ).toBe(false);
    });

    it("output 的 BranchSuggestion 多余未知字段 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          branch_suggestions: [
            { ...example.expected_output.branch_suggestions[0], auto_fork: true },
          ],
        }),
      ).toBe(false);
    });

    it("output 的 tool_runs 缺 tool_name → 拒绝（ToolRun 血缘必须）", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          tool_runs: [{ calls: 1 }],
        }),
      ).toBe(false);
    });
  });
});
