import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  loadSkill,
  loadAllowlistedSkills,
} from "../../src/skills/loader.js";

/**
 * propose-illustration-anchor skill 契约测试（Phase 34-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 34 5 工具 allowlist
 * （3 只读 + publish_illustration / attach_illustration_to_text action）、
 * 真实 loader 接受 pinned manifest（fail-closed 通过后才存在 LoadedSkill）、
 * registry-valid fail-closed（未知工具/未声明权限/schema drift）、双 fixture
 * 通过 schema、schema-mismatch 负面用例、取消语义、Phase 34 边界
 * （candidate-only proposal → Web Approval → deterministic publisher；
 * 绝不发布）、26-06 normalization trail、IllustrationAnchorProposal 信封字段
 * （D-34-01..D-34-04）。不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/propose-illustration-anchor/", import.meta.url);

// 返回 any：JSON.parse 的产物是动态 JSON；`as SkillManifest` / `Value.Check`
// 需要宽松类型（25.2-05 tsc 门禁修复，不改断言语义）。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 34-05 注册的 16 个域工具（域工具全集；33-05 14 个 + Phase 34
 *  publish_illustration / attach_illustration_to_text action）。 */
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
] as const;

/** Phase 34 编排 allowlist：3 个只读域工具 + 2 个 action 工具。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "publish_illustration",
  "attach_illustration_to_text",
] as const;

/** Phase 34 声明的审批动作集合：publish_illustration / attach_illustration_to_text
 *  都要求 Web ApprovalRequest（D-11/D-15）。 */
const DECLARED_APPROVAL_ACTIONS: readonly string[] = [
  "publish_illustration",
  "attach_illustration_to_text",
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
 * approval_required_for 只允许 Phase 34 声明的 action（publish_illustration /
 * attach_illustration_to_text，二者都要求 Web ApprovalRequest）。返回错误列表。
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

describe("propose-illustration-anchor skill package", () => {
  describe("真实 loader 接受 pinned manifest（34-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("propose-illustration-anchor");
      expect(skill.name).toBe("propose-illustration-anchor");
      expect(skill.version).toBe("1.0.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor.sort()).toEqual(
        [...DECLARED_APPROVAL_ACTIONS].sort(),
      );
      expect(skill.forbiddenSpaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "illustration:write",
          "illustration:publish",
          "approval_request",
          "publisher",
        ]),
      );
      expect(skill.readPermissions).toEqual(
        expect.arrayContaining(["canon", "illustration"]),
      );
      expect(skill.instructions.length).toBeGreaterThan(0);
      expect(typeof skill.validateInput).toBe("function");
      expect(typeof skill.validateOutput).toBe("function");
    });

    it("pinned manifest 的 input 通过真实 loader 编译的校验器", () => {
      const skill = loadSkill("propose-illustration-anchor");
      const example = readSkillJson("examples/basic.json") as { input: any };
      expect(skill.validateInput(example.input)).toBe(true);
    });

    it("loadAllowlistedSkills 包含 propose-illustration-anchor（ResourceLoader allowlist 注册）", () => {
      const names = loadAllowlistedSkills().map((s) => s.name);
      expect(names).toContain("propose-illustration-anchor");
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
        "chapter_id",
        "chapter_number",
        "proposal_key",
        "source_snapshot_id",
        "source_snapshot_hash",
        "source_start",
        "source_end",
        "excerpt",
        "anchor_hash",
        "chapter_content_hash",
        "asset_revision_id",
        "presentation",
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
      expect(schema.required).toContain("illustration_anchor_proposal");
    });

    it("output.schema.json 物化 IllustrationAnchorProposal 信封（D-34-01..D-34-04）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("illustration_anchor_proposal");
      expect(schema.properties.schema_version.const).toBe(
        "illustration-anchor-proposal.v1",
      );
      for (const field of [
        "evidence_refs",
        "input_hash",
        "model_lineage",
        "source_versions",
        "producing_skill",
        "producing_skill_version",
        "skill_version_id",
        "illustration_anchor_proposal",
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

    it("illustration_anchor_proposal 负载声明完整 branch-aware 血缘（proposal_status 恒为 proposed）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const payload = schema.properties.illustration_anchor_proposal;
      expect(payload.required).toEqual(
        expect.arrayContaining([
          "schema_version",
          "artifact_kind",
          "proposal_key",
          "authority_space",
          "chapter_id",
          "chapter_number",
          "source_snapshot_id",
          "source_snapshot_hash",
          "range",
          "excerpt",
          "anchor_hash",
          "chapter_content_hash",
          "proposal_asset_revision_id",
          "presentation",
          "requested_action",
          "proposal_status",
        ]),
      );
      // Phase 34 唯一状态机：proposal_status 枚举含 proposed/pending_approval/valid，
      // 但 finalize 写入时恒为 proposed（approval bypass → schema/服务端拒绝）。
      expect(payload.properties.proposal_status.enum).toEqual([
        "proposed",
        "pending_approval",
        "valid",
      ]);
      expect(payload.properties.schema_version.const).toBe(
        "illustration-anchor-proposal.v1",
      );
      expect(payload.properties.artifact_kind.const).toBe(
        "illustration_anchor_proposal",
      );
      expect(payload.properties.authority_space.enum).toEqual([
        "original",
        "derivative",
      ]);
      expect(payload.properties.anchor_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(payload.properties.requested_action.enum).toEqual([
        "publish_illustration",
        "attach_illustration_to_text",
      ]);
    });

    it("input.schema.json 声明 Phase 34 输入锚（确定性锚点服务按引用消费）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of [
        "novel_id",
        "branch",
        "fork",
        "chapter_id",
        "chapter_number",
        "proposal_key",
        "source_snapshot_id",
        "source_snapshot_hash",
        "source_start",
        "source_end",
        "paragraph_start",
        "paragraph_end",
        "excerpt",
        "anchor_hash",
        "chapter_content_hash",
        "asset_revision_id",
        "presentation",
        "requested_actions",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.asset_revision_id.minimum).toBe(1);
      expect(schema.properties.anchor_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(schema.properties.requested_actions.items.enum).toEqual([
        "publish_illustration",
        "attach_illustration_to_text",
      ]);
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("propose-illustration-anchor");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 5 个 Phase 34 域工具（3 只读 + 2 action）", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      // 全部是注册域工具（loader 会校验 ⊆ DOMAIN_TOOL_NAMES）。
      for (const tool of manifest.allowed_tools) {
        expect(REGISTERED_DOMAIN_TOOLS).toContain(tool);
      }
    });

    it("allowed_tools 含两个 action 工具（publish_illustration / attach_illustration_to_text）", () => {
      expect(manifest.allowed_tools).toContain("publish_illustration");
      expect(manifest.allowed_tools).toContain("attach_illustration_to_text");
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 恰为两个 Phase 34 action（Web ApprovalRequest）", () => {
      expect(manifest.approval_required_for.sort()).toEqual(
        [...DECLARED_APPROVAL_ACTIONS].sort(),
      );
    });

    it("forbidden_spaces 覆盖 canon:original、illustration:write/publish、approval_request、publisher", () => {
      expect(manifest.forbidden_spaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "illustration:write",
          "illustration:publish",
          "approval_request",
          "publisher",
        ]),
      );
    });

    it("read_permissions 声明 canon/illustration 只读", () => {
      expect(manifest.read_permissions).toEqual(
        expect.arrayContaining(["canon", "illustration"]),
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
        allowed_tools: [...manifest.allowed_tools, "publish_everything"],
      });
      expect(errors).toContain("unknown tools: publish_everything");
    });

    it("拒绝空 allowed_tools", () => {
      const errors = validateSkillContract({ ...manifest, allowed_tools: [] });
      expect(errors).toContain("allowed_tools must be non-empty");
    });

    it("拒绝非空 write_permissions（Agent 零域写入）", () => {
      const errors = validateSkillContract({
        ...manifest,
        write_permissions: ["illustration"],
      });
      expect(errors).toContain("agent must declare empty write_permissions");
    });

    it("拒绝未声明的审批动作（agent 不能自行声明 approval action）", () => {
      const errors = validateSkillContract({
        ...manifest,
        approval_required_for: ["illustration:publish"],
      });
      expect(errors).toContain(
        "undeclared approval actions: illustration:publish",
      );
    });

    it("schema drift：output type 是声明的 illustration_anchor_proposal（真实 loader 编译通过）", () => {
      expect(() => loadSkill("propose-illustration-anchor")).not.toThrow();
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("illustration_anchor_proposal");
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, example.input)).toBe(true);
    });

    it("examples/basic.json expected_output（IllustrationAnchorProposal）通过 output.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("tests/basic.json input 通过 input.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, fixture.input)).toBe(true);
    });

    it("tests/basic.json expected_output（derivative IllustrationAnchorProposal）通过 output.schema 校验", () => {
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
      expect(
        Value.Check(inputSchema, {
          branch: null,
          fork: null,
          chapter_id: 31,
          chapter_number: 1,
          proposal_key: "anchor-main",
          source_snapshot_id: "ss-1",
          source_snapshot_hash: "4".repeat(64),
          source_start: 0,
          source_end: 10,
          excerpt: "abc",
          anchor_hash: "a".repeat(64),
          chapter_content_hash: "b".repeat(64),
          asset_revision_id: 1,
          presentation: { caption: "c", alt_text: "a", citation: "cit" },
          requested_actions: ["publish_illustration"],
        }),
      ).toBe(false);
    });

    it("input 缺 requested_actions → 拒绝（Phase 34 action 必须声明）", () => {
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
          requested_actions: ["publish_everything"],
        }),
      ).toBe(false);
    });

    it("input 的 anchor_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          anchor_hash: "not-a-hash",
        }),
      ).toBe(false);
    });

    it("input 的 source_end 非法（非正数）→ 拒绝（精确 source span 必须）", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          source_start: 50,
          source_end: 0,
        }),
      ).toBe(false);
    });

    it("input 的 presentation 缺 citation → 拒绝（可访问 copy 契约 D-34-02）", () => {
      const presentation = example.input.presentation;
      const { citation, ...brokenCopy } = presentation;
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          presentation: { ...brokenCopy },
        }),
      ).toBe(false);
    });

    it("output 的 type 非法（既非 illustration_anchor_proposal）→ 拒绝", () => {
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
          schema_version: "illustration-anchor-proposal.v2",
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

    it("output 的 illustration_anchor_proposal.proposal_status 非 proposed 枚举（approval bypass）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          illustration_anchor_proposal: {
            ...example.expected_output.illustration_anchor_proposal,
            proposal_status: "published",
          },
        }),
      ).toBe(false);
    });

    it("output 的 illustration_anchor_proposal 缺 anchor_hash → 拒绝（lineage 必须）", () => {
      const payload = example.expected_output.illustration_anchor_proposal;
      const { anchor_hash, ...broken } = payload;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          illustration_anchor_proposal: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 的 illustration_anchor_proposal 缺 proposal_status → 拒绝（Phase 34 状态机必须）", () => {
      const payload = example.expected_output.illustration_anchor_proposal;
      const { proposal_status, ...broken } = payload;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          illustration_anchor_proposal: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 的 illustration_anchor_proposal 缺 range → 拒绝（精确 source span 必须）", () => {
      const payload = example.expected_output.illustration_anchor_proposal;
      const { range, ...broken } = payload;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          illustration_anchor_proposal: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 缺 illustration_anchor_proposal 负载 → 拒绝", () => {
      const { illustration_anchor_proposal, ...broken } = example.expected_output;
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

    it("output 的 normalization 缺 raw_hash → 拒绝", () => {
      const trail = example.expected_output.normalization;
      const { raw_hash, ...broken } = trail;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          normalization: { ...broken },
        }),
      ).toBe(false);
    });
  });

  describe("取消与 Phase 34 边界（fail-closed 语义）", () => {
    it("SKILL.md 声明取消 → cancelled 且零 artifact/revision 写入", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("cancel_requested");
      expect(skill).toContain("cancelled");
      expect(skill).toContain("0 artifact 行");
      expect(skill).toContain("0 revision 行");
    });

    it("SKILL.md 声明 Phase 34 边界（candidate-only proposal + Web Approval + deterministic publisher）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "ApprovalRequest",
        "deterministic publisher",
        "publish",
        "绝不",
        "fail closed",
        "pending_approval",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 approval_required_for 两个 action（publish_illustration / attach_illustration_to_text）", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("approval_required_for");
      expect(skill).toContain("publish_illustration");
      expect(skill).toContain("attach_illustration_to_text");
      expect(skill).toContain("Web ApprovalRequest");
    });

    it("SKILL.md 声明唯一状态机 proposed → pending_approval → valid 只由服务端推进", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "proposed → pending_approval → valid",
        "仅前向",
        "proposal_status",
        "服务端",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 wrong owner/branch/fork/stale/schema drift/forbidden Tool → 稳定 blocked/cancelled 零写入", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "wrong owner",
        "wrong branch/fork",
        "stale",
        "schema drift",
        "forbidden Tool/action",
        "零写入",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 Action 工具只创建候选 proposal + pending ApprovalRequest（绝不发布）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "pending ApprovalRequest",
        "candidate-only",
        "绝不发布",
        "确定性 publisher",
        "IllustrationAnchorProposal",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 D-34-01 精确 source span：offset/hash 不匹配即 stale，绝不静默移位", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["D-34-01", "stale", "绝不", "静默"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 allowlist 外工具 → fail closed", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("allowlist 外");
    });
  });

  describe("candidate-only 域纪律（D-34-01..D-34-04）", () => {
    it("SKILL.md 声明 proposal 绝不自动进入 reader/export / Canon", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["候选", "reader/export", "绝不", "proposal"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明确定性 publisher 拥有 approved publication 权威", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["publisher", "approved", "原子校验", "valid anchor"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明每次 run 绑定完整血缘（owner/novel/authority_space/branch/fork/SkillRun/ToolRuns/hash/lineage）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "owner",
        "novel",
        "authority_space",
        "branch/fork",
        "SkillRun",
        "ToolRuns",
        "source/input hashes",
        "model/runtime lineage",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 Web Approval 权威只在 FastAPI（浏览器只渲染，D-11）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["浏览器只渲染", "FastAPI 是唯一决策权威", "D-11"]) {
        expect(skill).toContain(token);
      }
    });
  });

  describe("Phase 34 normalization trail 正/负用例", () => {
    const outputSchema = readSkillJson("output.schema.json");
    const example = readSkillJson("examples/basic.json") as { expected_output: any };
    const trail = example.expected_output.normalization as Record<string, any>;

    it("合法 normalization trail（noop 修复）→ 通过", () => {
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("带 alias/container 修复动作的 trail（path/action/after）→ 通过", () => {
      const envelope = {
        ...example.expected_output,
        normalization: {
          raw_hash: "0".repeat(64),
          repaired_hash: "0".repeat(64),
          normalization_actions: [
            { path: "producing_skill", action: "alias", before: "skill_name", after: "propose-illustration-anchor", reason: "declared alias" },
            { path: "tool_runs", action: "container_shape", before: { tool: "x" }, after: [{ tool: "x" }], reason: "declared wrap" },
          ],
          warnings: ["declared repairs applied"],
        },
      };
      expect(Value.Check(outputSchema, envelope)).toBe(true);
    });

    it("normalization_actions 项 action 非声明修复种类 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          normalization: {
            ...trail,
            normalization_actions: [
              { path: "illustration_anchor_proposal", action: "hallucinate_fact", after: { x: 1 } },
            ],
          },
        }),
      ).toBe(false);
    });
  });
});
