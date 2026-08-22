import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  loadSkill,
  loadAllowlistedSkills,
} from "../../src/skills/loader.js";

/**
 * illustrate-derivative-scene skill 契约测试（Phase 38-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 38 8 工具 allowlist
 * （7 只读 + publish_derivative_visual action）、真实 loader 接受 pinned
 * manifest（fail-closed 通过后才存在 LoadedSkill）、registry-valid fail-closed
 * （未知工具/未声明权限/schema drift）、双 fixture 通过 schema、
 * schema-mismatch 负面用例、BranchVisualBibleArtifact / BranchIllustrationRevision
 * 完整 branch-aware 血缘契约（authority_space 恒为 derivative + fork、
 * review_state 恒为 candidate、approval_request_id/publish_lineage 由服务端
 * 分配）、Phase 38 边界（candidate-only 产物 → publish_derivative_visual
 * approval → review seam 确定性发布；Agent 绝不直接写 Original Visual Bible /
 * 域表 / published 状态）、26-06 normalization trail。不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/illustrate-derivative-scene/", import.meta.url);

// 返回 any：JSON.parse 的产物是动态 JSON；`Value.Check` 需要宽松类型。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 38-05 注册的 21 个域工具（域工具全集；37-05 20 个 + Phase 38
 *  publish_derivative_visual action）。 */
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
  "publish_derivative_visual",
] as const;

/** Phase 38 编排 allowlist：7 个只读域工具 + 1 个 action 工具。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "get_timeline",
  "get_relationships",
  "get_clues",
  "get_narrative_memory",
  "publish_derivative_visual",
] as const;

/** Phase 38 声明的审批动作集合：publish_derivative_visual 要求独立 Web
 * ApprovalRequest（D-11/D-15）；只有确定性 review seam 在批准后物化。 */
const DECLARED_APPROVAL_ACTIONS: readonly string[] = [
  "publish_derivative_visual",
];

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

/** BranchIllustrationRevision 必填血缘字段（D-38-03/D-38-04）。 */
const REVISION_REQUIRED_FIELDS = [
  "schema_version",
  "artifact_kind",
  "authority_space",
  "fork",
  "visual_version",
  "source_snapshot",
  "scene_spec_hash",
  "candidate_asset",
  "identity_lineage",
  "source_refs",
  "generator_lineage",
  "divergence_manifest_hash",
  "consistency_verdict",
  "validator_report",
  "review_state",
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
 * approval_required_for 只允许 Phase 38 声明的 action
 * （publish_derivative_visual，独立 Web ApprovalRequest）。返回错误列表。
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

describe("illustrate-derivative-scene skill package", () => {
  describe("真实 loader 接受 pinned manifest（38-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("illustrate-derivative-scene");
      expect(skill.name).toBe("illustrate-derivative-scene");
      expect(skill.version).toBe("1.0.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor).toEqual(["publish_derivative_visual"]);
      expect(skill.forbiddenSpaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "user_interpretation",
          "derivative:autosave",
          "derivative:direct_write",
          "derivative_visual:write",
          "approval_request",
          "review_service",
          "published_assets",
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
      const skill = loadSkill("illustrate-derivative-scene");
      const example = readSkillJson("examples/basic.json") as { input: any };
      expect(skill.validateInput(example.input)).toBe(true);
    });

    it("loadAllowlistedSkills 包含 illustrate-derivative-scene（ResourceLoader allowlist 注册）", () => {
      const names = loadAllowlistedSkills().map((s) => s.name);
      expect(names).toContain("illustrate-derivative-scene");
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
        "visual_fork_version_id",
        "scene_spec_hash",
        "candidate_asset_id",
        "source_snapshot_hash",
        "evidence_refs",
        "requested_action",
      ]) {
        expect(schema.required).toContain(field);
      }
      expect(schema.additionalProperties).toBe(false);
    });

    it("output.schema.json 是合法 JSON 且为 draft-07 对象 schema", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
      expect(schema.type).toBe("object");
      expect(schema.required).toContain("revision");
      expect(schema.required).toContain("evidence_refs");
    });

    it("output.schema.json 物化 BranchVisualBibleArtifact 信封（D-38-03/D-38-04）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("branch_visual_bible");
      expect(schema.properties.schema_version.const).toBe("branch-visual-bible.v1");
      for (const field of [
        "evidence_refs",
        "input_hash",
        "model_lineage",
        "source_versions",
        "producing_skill",
        "producing_skill_version",
        "skill_version_id",
        "revision",
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

    it("revision 负载声明完整 branch-aware 血缘（authority_space 恒为 derivative）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const revision = schema.definitions.branch_illustration_revision;
      for (const field of REVISION_REQUIRED_FIELDS) {
        expect(revision.required).toContain(field);
      }
      expect(revision.properties.schema_version.const).toBe("branch-illustration-revision.v1");
      expect(revision.properties.artifact_kind.const).toBe("branch_illustration_revision");
      expect(revision.properties.authority_space.const).toBe("derivative");
      expect(revision.properties.fork.minLength).toBe(1);
      expect(revision.properties.review_state.const).toBe("candidate");
      expect(revision.properties.divergence_manifest_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(revision.properties.scene_spec_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(revision.properties.consistency_verdict.enum).toEqual([
        "pass",
        "concern",
        "fail",
        "unavailable",
      ]);
      // approval_request_id / publish_lineage 由服务端分配，模型输出不含。
      expect(revision.properties.approval_request_id).toBeDefined();
      expect(revision.properties.publish_lineage).toBeDefined();
      expect(revision.additionalProperties).toBe(false);
    });

    it("input.schema.json 声明 Phase 38 输入锚（确定性 derivative visual 域按引用消费）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of [
        "novel_id",
        "branch",
        "fork",
        "visual_fork_version_id",
        "scene_spec_hash",
        "candidate_asset_id",
        "source_snapshot_id",
        "source_snapshot_hash",
        "evidence_refs",
        "requested_action",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.scene_spec_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(schema.properties.source_snapshot_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(schema.properties.requested_action.items.enum).toEqual([
        "publish_derivative_visual",
      ]);
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("illustrate-derivative-scene");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 8 个 Phase 38 域工具（7 只读 + 1 action）", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      // 全部是注册域工具（loader 会校验 ⊆ DOMAIN_TOOL_NAMES）。
      for (const tool of manifest.allowed_tools) {
        expect(REGISTERED_DOMAIN_TOOLS).toContain(tool);
      }
    });

    it("allowed_tools 含 publish_derivative_visual action（且不含任何 action 越界）", () => {
      expect(manifest.allowed_tools).toContain("publish_derivative_visual");
      expect(manifest.allowed_tools).not.toContain("apply_derivative_edit");
      expect(manifest.allowed_tools).not.toContain("create_canon_fork");
      expect(manifest.allowed_tools).not.toContain("publish_illustration");
      expect(manifest.allowed_tools).not.toContain("attach_illustration_to_text");
      expect(manifest.allowed_tools).not.toContain("generate_image_candidate");
      expect(manifest.allowed_tools).not.toContain("allow_divergence");
      expect(manifest.allowed_tools).not.toContain("publish_derivative_revision");
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 恰为 publish_derivative_visual（独立 Web ApprovalRequest）", () => {
      expect(manifest.approval_required_for.sort()).toEqual([
        "publish_derivative_visual",
      ]);
    });

    it("forbidden_spaces 覆盖 canon:original、derivative_visual:write、approval_request、review_service、published_assets", () => {
      expect(manifest.forbidden_spaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "user_interpretation",
          "derivative:autosave",
          "derivative:direct_write",
          "derivative_visual:write",
          "approval_request",
          "review_service",
          "published_assets",
        ]),
      );
    });

    it("read_permissions 声明 canon/fanfiction_canon/visual_bible/scene_spec 只读", () => {
      expect(manifest.read_permissions).toEqual(
        expect.arrayContaining(["canon", "fanfiction_canon", "visual_bible", "scene_spec"]),
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
        allowed_tools: [...manifest.allowed_tools, "publish_derivative_visual_directly"],
      });
      expect(errors).toContain("unknown tools: publish_derivative_visual_directly");
    });

    it("拒绝空 allowed_tools", () => {
      const errors = validateSkillContract({ ...manifest, allowed_tools: [] });
      expect(errors).toContain("allowed_tools must be non-empty");
    });

    it("拒绝非空 write_permissions（Agent 零域写入）", () => {
      const errors = validateSkillContract({
        ...manifest,
        write_permissions: ["derivative_visual:write"],
      });
      expect(errors).toContain("agent must declare empty write_permissions");
    });

    it("拒绝未声明的审批动作（agent 不能自行声明 approval action）", () => {
      const errors = validateSkillContract({
        ...manifest,
        approval_required_for: ["review_service"],
      });
      expect(errors).toContain(
        "undeclared approval actions: review_service",
      );
    });

    it("schema drift：output type 是声明的 branch_visual_bible（真实 loader 编译通过）", () => {
      expect(() => loadSkill("illustrate-derivative-scene")).not.toThrow();
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("branch_visual_bible");
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, example.input)).toBe(true);
    });

    it("examples/basic.json expected_output（BranchVisualBibleArtifact）通过 output.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("tests/basic.json input 通过 input.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, fixture.input)).toBe(true);
    });

    it("tests/basic.json expected_output（candidate BranchVisualBibleArtifact）通过 output.schema 校验", () => {
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

    it("input 缺 visual_fork_version_id → 拒绝（已批准 fork version 必须）", () => {
      const { visual_fork_version_id, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 缺 candidate_asset_id → 拒绝（已存储候选必须）", () => {
      const { candidate_asset_id, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 的 scene_spec_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          scene_spec_hash: "not-a-hash",
        }),
      ).toBe(false);
    });

    it("input 的 requested_action 含未知 action → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          requested_action: ["publish_derivative_visual_directly"],
        }),
      ).toBe(false);
    });

    it("input 的 evidence_refs 为空数组 → 拒绝（leaf-evidence 资格门）", () => {
      expect(
        Value.Check(inputSchema, { ...example.input, evidence_refs: [] }),
      ).toBe(false);
    });

    it("input 多余未知字段（additionalProperties:false）→ 拒绝", () => {
      expect(
        Value.Check(inputSchema, { ...example.input, hacked: true }),
      ).toBe(false);
    });

    it("output 的 type 非法（既非 branch_visual_bible）→ 拒绝", () => {
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
          schema_version: "branch-visual-bible.v2",
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

    it("output 缺 revision 负载 → 拒绝", () => {
      const { revision, ...broken } = example.expected_output;
      expect(Value.Check(outputSchema, { ...broken })).toBe(false);
    });

    it("output 的 revision.authority_space 非法 → 拒绝（derivative-only）", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          revision: {
            ...example.expected_output.revision,
            authority_space: "original",
          },
        }),
      ).toBe(false);
    });

    it("output 的 revision.review_state 非 candidate → 拒绝（approval bypass）", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          revision: {
            ...example.expected_output.revision,
            review_state: "approved",
          },
        }),
      ).toBe(false);
    });

    it("output 的 revision 缺 fork → 拒绝（derivative mode 必须携带 fork）", () => {
      const { fork, ...broken } = example.expected_output.revision;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          revision: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 的 revision.consistency_verdict 非法 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          revision: {
            ...example.expected_output.revision,
            consistency_verdict: "bogus",
          },
        }),
      ).toBe(false);
    });

    it("output 的 revision.divergence_manifest_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          revision: {
            ...example.expected_output.revision,
            divergence_manifest_hash: "not-a-hash",
          },
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
