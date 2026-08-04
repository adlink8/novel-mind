import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  loadSkill,
  loadAllowlistedSkills,
} from "../../src/skills/loader.js";

/**
 * edit-derivative-story skill 契约测试（Phase 36-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 36 7 工具 allowlist
 * （6 只读 + apply_derivative_edit action）、真实 loader 接受 pinned manifest
 * （fail-closed 通过后才存在 LoadedSkill）、registry-valid fail-closed（未知工具/
 * 未声明权限/schema drift）、双 fixture 通过 schema、schema-mismatch 负面用例、
 * 取消语义、Phase 36 边界（candidate-only DerivativeEditProposal → Web Approval
 * → deterministic Revision Service；Agent 绝不直接写 Original Canon / user
 * autosave / published 状态；user_autosave 与 agent_proposal 分离端点/事件/actor
 * 标签/CAS 路径）、26-06 normalization trail、DerivativeEditProposalArtifact
 * 信封字段（D-36-01..D-36-04）。不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/edit-derivative-story/", import.meta.url);

// 返回 any：JSON.parse 的产物是动态 JSON；`as SkillManifest` / `Value.Check`
// 需要宽松类型（25.2-05 tsc 门禁修复，不改断言语义）。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 36-05 注册的 18 个域工具（域工具全集；35-05 17 个 + Phase 36
 *  apply_derivative_edit action）。 */
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
] as const;

/** Phase 36 编排 allowlist：6 个只读域工具 + 1 个 action 工具。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_novel",
  "get_chapter",
  "get_timeline",
  "get_relationships",
  "get_clues",
  "get_narrative_memory",
  "apply_derivative_edit",
] as const;

/** Phase 36 声明的审批动作集合：apply_derivative_edit 要求 Web ApprovalRequest
 * （D-11/D-15），Approval 后由确定性 Revision Service 应用。 */
const DECLARED_APPROVAL_ACTIONS: readonly string[] = ["apply_derivative_edit"];

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
 * approval_required_for 只允许 Phase 36 声明的 action（apply_derivative_edit，要求
 * Web ApprovalRequest）。返回错误列表。
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

describe("edit-derivative-story skill package", () => {
  describe("真实 loader 接受 pinned manifest（36-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("edit-derivative-story");
      expect(skill.name).toBe("edit-derivative-story");
      expect(skill.version).toBe("1.0.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor).toEqual(["apply_derivative_edit"]);
      expect(skill.forbiddenSpaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "user_interpretation",
          "derivative:autosave",
          "derivative:direct_write",
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
      const skill = loadSkill("edit-derivative-story");
      const example = readSkillJson("examples/basic.json") as { input: any };
      expect(skill.validateInput(example.input)).toBe(true);
    });

    it("loadAllowlistedSkills 包含 edit-derivative-story（ResourceLoader allowlist 注册）", () => {
      const names = loadAllowlistedSkills().map((s) => s.name);
      expect(names).toContain("edit-derivative-story");
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
        "proposal_key",
        "base_revision",
        "content",
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
      expect(schema.required).toContain("proposal");
    });

    it("output.schema.json 物化 DerivativeEditProposal 信封（D-36-01..D-36-04）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("derivative_edit_proposal");
      expect(schema.properties.schema_version.const).toBe(
        "derivative-edit-proposal.v1",
      );
      for (const field of [
        "evidence_refs",
        "input_hash",
        "model_lineage",
        "source_versions",
        "producing_skill",
        "producing_skill_version",
        "skill_version_id",
        "proposal",
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

    it("proposal 负载声明完整 branch-aware 血缘（proposal_status 恒为 proposed）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const payload = schema.properties.proposal;
      expect(payload.required).toEqual(
        expect.arrayContaining([
          "schema_version",
          "artifact_kind",
          "proposal_key",
          "authority_space",
          "project_id",
          "chapter_id",
          "chapter_number",
          "base_revision",
          "content",
          "content_hash",
          "source_snapshot_id",
          "source_snapshot_hash",
          "proposal_status",
        ]),
      );
      // Phase 36 唯一状态机：proposal_status 枚举含 proposed/pending_approval/applied，
      // 但 finalize 写入时恒为 proposed（approval bypass → schema/服务端拒绝）。
      expect(payload.properties.proposal_status.enum).toEqual([
        "proposed",
        "pending_approval",
        "applied",
      ]);
      expect(payload.properties.schema_version.const).toBe(
        "derivative-edit-proposal.v1",
      );
      expect(payload.properties.artifact_kind.const).toBe("derivative_edit_proposal");
      expect(payload.properties.authority_space.const).toBe("derivative");
      expect(payload.properties.content_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(payload.properties.source_snapshot_hash.pattern).toBe("^[0-9a-f]{64}$");
    });

    it("input.schema.json 声明 Phase 36 输入锚（确定性 Revision Service 按引用消费）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of [
        "novel_id",
        "branch",
        "fork",
        "project_id",
        "chapter_id",
        "chapter_number",
        "proposal_key",
        "base_revision",
        "content",
        "source_snapshot_id",
        "source_snapshot_hash",
        "evidence_refs",
        "requested_actions",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.proposal_key.maxLength).toBe(160);
      expect(schema.properties.content.maxLength).toBe(50000);
      expect(schema.properties.base_revision.minimum).toBe(1);
      expect(schema.properties.source_snapshot_hash.pattern).toBe(
        "^[0-9a-f]{64}$",
      );
      expect(schema.properties.requested_actions.items.enum).toEqual([
        "apply_derivative_edit",
      ]);
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("edit-derivative-story");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 7 个 Phase 36 域工具（6 只读 + 1 action）", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      // 全部是注册域工具（loader 会校验 ⊆ DOMAIN_TOOL_NAMES）。
      for (const tool of manifest.allowed_tools) {
        expect(REGISTERED_DOMAIN_TOOLS).toContain(tool);
      }
    });

    it("allowed_tools 含 action 工具 apply_derivative_edit（且不含任何 action 越界）", () => {
      expect(manifest.allowed_tools).toContain("apply_derivative_edit");
      expect(manifest.allowed_tools).not.toContain("publish_illustration");
      expect(manifest.allowed_tools).not.toContain("attach_illustration_to_text");
      expect(manifest.allowed_tools).not.toContain("create_canon_fork");
      expect(manifest.allowed_tools).not.toContain("generate_image_candidate");
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 恰为 apply_derivative_edit（Web ApprovalRequest）", () => {
      expect(manifest.approval_required_for).toEqual(["apply_derivative_edit"]);
    });

    it("forbidden_spaces 覆盖 canon:original、user_interpretation、derivative:autosave/direct_write、approval_request、revision_service", () => {
      expect(manifest.forbidden_spaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "user_interpretation",
          "derivative:autosave",
          "derivative:direct_write",
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
      expect(manifest.budget.max_calls).toBe(30);
      expect(manifest.budget.max_input_tokens).toBe(30000);
      expect(manifest.budget.max_output_tokens).toBe(10000);
      expect(String(manifest.budget.max_cost_usd)).toBe("3.00");
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
        allowed_tools: [...manifest.allowed_tools, "apply_derivative_edit_directly"],
      });
      expect(errors).toContain("unknown tools: apply_derivative_edit_directly");
    });

    it("拒绝空 allowed_tools", () => {
      const errors = validateSkillContract({ ...manifest, allowed_tools: [] });
      expect(errors).toContain("allowed_tools must be non-empty");
    });

    it("拒绝非空 write_permissions（Agent 零域写入）", () => {
      const errors = validateSkillContract({
        ...manifest,
        write_permissions: ["derivative:direct_write"],
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

    it("schema drift：output type 是声明的 derivative_edit_proposal（真实 loader 编译通过）", () => {
      expect(() => loadSkill("edit-derivative-story")).not.toThrow();
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("derivative_edit_proposal");
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, example.input)).toBe(true);
    });

    it("examples/basic.json expected_output（DerivativeEditProposal）通过 output.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("tests/basic.json input 通过 input.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, fixture.input)).toBe(true);
    });

    it("tests/basic.json expected_output（derivative DerivativeEditProposal）通过 output.schema 校验", () => {
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

    it("input 缺 chapter_id → 拒绝", () => {
      const { chapter_id, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 缺 proposal_key → 拒绝（proposal 标识必须）", () => {
      const { proposal_key, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 缺 base_revision → 拒绝（CAS 锚必须）", () => {
      const { base_revision, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 缺 requested_actions → 拒绝（Phase 36 action 必须声明）", () => {
      const { requested_actions, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 多余未知字段（additionalProperties:false）→ 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          hacked: true,
        }),
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

    it("input 的 source_snapshot_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          source_snapshot_hash: "not-a-hash",
        }),
      ).toBe(false);
    });

    it("input 的 evidence_refs 为空数组 → 拒绝（leaf-evidence 资格门）", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          evidence_refs: [],
        }),
      ).toBe(false);
    });

    it("input 的 content 为空字符串 → 拒绝（候选 patch 必须非空）", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          content: "",
        }),
      ).toBe(false);
    });

    it("input 的 base_revision 为 0 → 拒绝（CAS 锚必须为正）", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          base_revision: 0,
        }),
      ).toBe(false);
    });

    it("output 的 type 非法（既非 derivative_edit_proposal）→ 拒绝", () => {
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
          schema_version: "derivative-edit-proposal.v2",
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

    it("output 的 proposal.proposal_status 非法（published 不在枚举；直接应用伪造）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          proposal: {
            ...example.expected_output.proposal,
            proposal_status: "published",
          },
        }),
      ).toBe(false);
    });

    it("output 的 proposal.content_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          proposal: {
            ...example.expected_output.proposal,
            content_hash: "not-a-hash",
          },
        }),
      ).toBe(false);
    });

    it("output 的 proposal 缺 base_revision → 拒绝（stale base 必须 fail closed）", () => {
      const payload = example.expected_output.proposal;
      const { base_revision, ...broken } = payload;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          proposal: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 缺 proposal 负载 → 拒绝", () => {
      const { proposal, ...broken } = example.expected_output;
      expect(Value.Check(outputSchema, { ...broken })).toBe(false);
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
