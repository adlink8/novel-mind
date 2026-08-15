import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  loadSkill,
  loadAllowlistedSkills,
} from "../../src/skills/loader.js";

/**
 * create-canon-fork skill 契约测试（Phase 35-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 35 8 工具 allowlist
 * （7 只读 + create_canon_fork action）、真实 loader 接受 pinned manifest
 * （fail-closed 通过后才存在 LoadedSkill）、registry-valid fail-closed（未知工具/
 * 未声明权限/schema drift）、双 fixture 通过 schema、schema-mismatch 负面用例、
 * 取消语义、Phase 35 边界（candidate-only fork → Web Approval → deterministic
 * Fork materializer；Original Canon 不可变；绝不物化）、26-06 normalization
 * trail、CanonForkProposal + CanonDeltaArtifact 信封字段（D-35-01..D-35-04）。
 * 不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/create-canon-fork/", import.meta.url);

// 返回 any：JSON.parse 的产物是动态 JSON；`as SkillManifest` / `Value.Check`
// 需要宽松类型（25.2-05 tsc 门禁修复，不改断言语义）。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 35-05 注册的 17 个域工具（域工具全集；34-05 16 个 + Phase 35
 *  create_canon_fork action）。 */
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
] as const;

/** Phase 35 编排 allowlist：7 个只读域工具 + 1 个 action 工具。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "get_timeline",
  "get_relationships",
  "get_clues",
  "get_narrative_memory",
  "create_canon_fork",
] as const;

/** Phase 35 声明的审批动作集合：create_canon_fork 要求 Web ApprovalRequest
 * （D-11/D-15），Approval 后由 deterministic Fork materializer 物化。 */
const DECLARED_APPROVAL_ACTIONS: readonly string[] = ["create_canon_fork"];

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
 * approval_required_for 只允许 Phase 35 声明的 action（create_canon_fork，要求
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

describe("create-canon-fork skill package", () => {
  describe("真实 loader 接受 pinned manifest（35-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("create-canon-fork");
      expect(skill.name).toBe("create-canon-fork");
      expect(skill.version).toBe("1.0.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor).toEqual(["create_canon_fork"]);
      expect(skill.forbiddenSpaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "canon_fork:write",
          "canon_fork:materialize",
          "approval_request",
          "fork_materializer",
        ]),
      );
      expect(skill.readPermissions).toEqual(
        expect.arrayContaining(["canon", "canon_fork"]),
      );
      expect(skill.instructions.length).toBeGreaterThan(0);
      expect(typeof skill.validateInput).toBe("function");
      expect(typeof skill.validateOutput).toBe("function");
    });

    it("pinned manifest 的 input 通过真实 loader 编译的校验器", () => {
      const skill = loadSkill("create-canon-fork");
      const example = readSkillJson("examples/basic.json") as { input: any };
      expect(skill.validateInput(example.input)).toBe(true);
    });

    it("loadAllowlistedSkills 包含 create-canon-fork（ResourceLoader allowlist 注册）", () => {
      const names = loadAllowlistedSkills().map((s) => s.name);
      expect(names).toContain("create-canon-fork");
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
        "fork_key",
        "delta_key",
        "delta_content",
        "delta_evidence_refs",
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
      expect(schema.required).toContain("delta");
    });

    it("output.schema.json 物化 CanonForkProposal 信封（D-35-01..D-35-04）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("canon_fork_proposal");
      expect(schema.properties.schema_version.const).toBe(
        "canon-fork-proposal.v1",
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
        "delta",
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
          "fork_key",
          "source_version_key",
          "source_snapshot_id",
          "source_snapshot_hash",
          "through_chapter",
          "full_book_authorized",
          "cutoff_snapshot_hash",
          "scope_hash",
          "manifest_hash",
          "citation_lineage",
          "authorization",
          "proposal_status",
        ]),
      );
      // Phase 35 唯一状态机：proposal_status 枚举含 proposed/pending_approval/approved，
      // 但 finalize 写入时恒为 proposed（approval bypass → schema/服务端拒绝）。
      expect(payload.properties.proposal_status.enum).toEqual([
        "proposed",
        "pending_approval",
        "approved",
      ]);
      expect(payload.properties.schema_version.const).toBe(
        "canon-fork-proposal.v1",
      );
      expect(payload.properties.artifact_kind.const).toBe("canon_fork_proposal");
      expect(payload.properties.source_snapshot_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(payload.properties.manifest_hash.pattern).toBe("^[0-9a-f]{64}$");
    });

    it("delta 负载声明 base revision + content hash（stale base → fail closed）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const delta = schema.properties.delta;
      expect(delta.required).toEqual(
        expect.arrayContaining([
          "schema_version",
          "artifact_kind",
          "delta_key",
          "base_revision",
          "content",
          "content_hash",
          "delta_status",
        ]),
      );
      expect(delta.properties.schema_version.const).toBe("canon-delta.v1");
      expect(delta.properties.artifact_kind.const).toBe("canon_delta");
      expect(delta.properties.base_revision.pattern).toBe("^[0-9a-f]{64}$");
      expect(delta.properties.content_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(delta.properties.delta_status.enum).toEqual([
        "proposed",
        "pending_approval",
        "approved",
      ]);
    });

    it("input.schema.json 声明 Phase 35 输入锚（确定性 snapshot 服务按引用消费）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of [
        "novel_id",
        "branch",
        "fork",
        "fork_key",
        "requested_cutoff_chapter",
        "full_book_requested",
        "expected_source_snapshot_hash",
        "delta_key",
        "delta_content",
        "delta_evidence_refs",
        "requested_actions",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.fork_key.maxLength).toBe(128);
      expect(schema.properties.delta_content.maxLength).toBe(50000);
      expect(schema.properties.expected_source_snapshot_hash.pattern).toBe(
        "^[0-9a-f]{64}$",
      );
      expect(schema.properties.requested_actions.items.enum).toEqual([
        "create_canon_fork",
      ]);
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("create-canon-fork");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 8 个 Phase 35 域工具（7 只读 + 1 action）", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      // 全部是注册域工具（loader 会校验 ⊆ DOMAIN_TOOL_NAMES）。
      for (const tool of manifest.allowed_tools) {
        expect(REGISTERED_DOMAIN_TOOLS).toContain(tool);
      }
    });

    it("allowed_tools 含 action 工具 create_canon_fork（且不含任何 action 越界）", () => {
      expect(manifest.allowed_tools).toContain("create_canon_fork");
      expect(manifest.allowed_tools).not.toContain("publish_illustration");
      expect(manifest.allowed_tools).not.toContain("attach_illustration_to_text");
      expect(manifest.allowed_tools).not.toContain("generate_image_candidate");
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 恰为 create_canon_fork（Web ApprovalRequest）", () => {
      expect(manifest.approval_required_for).toEqual(["create_canon_fork"]);
    });

    it("forbidden_spaces 覆盖 canon:original、canon_fork:write/materialize、approval_request、fork_materializer", () => {
      expect(manifest.forbidden_spaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "canon_fork:write",
          "canon_fork:materialize",
          "approval_request",
          "fork_materializer",
        ]),
      );
    });

    it("read_permissions 声明 canon/canon_fork 只读", () => {
      expect(manifest.read_permissions).toEqual(
        expect.arrayContaining(["canon", "canon_fork"]),
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
        allowed_tools: [...manifest.allowed_tools, "materialize_fork_directly"],
      });
      expect(errors).toContain("unknown tools: materialize_fork_directly");
    });

    it("拒绝空 allowed_tools", () => {
      const errors = validateSkillContract({ ...manifest, allowed_tools: [] });
      expect(errors).toContain("allowed_tools must be non-empty");
    });

    it("拒绝非空 write_permissions（Agent 零域写入）", () => {
      const errors = validateSkillContract({
        ...manifest,
        write_permissions: ["canon_fork"],
      });
      expect(errors).toContain("agent must declare empty write_permissions");
    });

    it("拒绝未声明的审批动作（agent 不能自行声明 approval action）", () => {
      const errors = validateSkillContract({
        ...manifest,
        approval_required_for: ["canon_fork:materialize"],
      });
      expect(errors).toContain(
        "undeclared approval actions: canon_fork:materialize",
      );
    });

    it("schema drift：output type 是声明的 canon_fork_proposal（真实 loader 编译通过）", () => {
      expect(() => loadSkill("create-canon-fork")).not.toThrow();
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("canon_fork_proposal");
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, example.input)).toBe(true);
    });

    it("examples/basic.json expected_output（CanonForkProposal + CanonDeltaArtifact）通过 output.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("tests/basic.json input 通过 input.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, fixture.input)).toBe(true);
    });

    it("tests/basic.json expected_output（derivative CanonForkProposal）通过 output.schema 校验", () => {
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

    it("input 缺 fork_key → 拒绝（fork 标识必须）", () => {
      const { fork_key, ...broken } = example.input;
      expect(Value.Check(inputSchema, { ...broken })).toBe(false);
    });

    it("input 缺 requested_actions → 拒绝（Phase 35 action 必须声明）", () => {
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
          requested_actions: ["materialize_fork"],
        }),
      ).toBe(false);
    });

    it("input 的 expected_source_snapshot_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          expected_source_snapshot_hash: "not-a-hash",
        }),
      ).toBe(false);
    });

    it("input 的 delta_evidence_refs 为空数组 → 拒绝（leaf-evidence 资格门）", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          delta_evidence_refs: [],
        }),
      ).toBe(false);
    });

    it("input 的 delta_content 为空字符串 → 拒绝（候选 delta 必须非空）", () => {
      expect(
        Value.Check(inputSchema, {
          ...example.input,
          delta_content: "",
        }),
      ).toBe(false);
    });

    it("output 的 type 非法（既非 canon_fork_proposal）→ 拒绝", () => {
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
          schema_version: "canon-fork-proposal.v2",
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

    it("output 的 proposal.proposal_status 非法（published 不在枚举；物化伪造）→ 拒绝", () => {
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

    it("output 的 delta.delta_status 非法（published 不在枚举；物化伪造）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          delta: {
            ...example.expected_output.delta,
            delta_status: "published",
          },
        }),
      ).toBe(false);
    });

    it("output 的 delta.content_hash 非 64 位 hex → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          delta: {
            ...example.expected_output.delta,
            content_hash: "not-a-hash",
          },
        }),
      ).toBe(false);
    });

    it("output 的 proposal 缺 manifest_hash → 拒绝（frozen manifest 必须）", () => {
      const payload = example.expected_output.proposal;
      const { manifest_hash, ...broken } = payload;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          proposal: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 的 delta 缺 base_revision → 拒绝（stale base 必须 fail closed）", () => {
      const delta = example.expected_output.delta;
      const { base_revision, ...broken } = delta;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          delta: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 缺 delta 负载 → 拒绝", () => {
      const { delta, ...broken } = example.expected_output;
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
