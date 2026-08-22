import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  loadSkill,
  loadAllowlistedSkills,
} from "../../src/skills/loader.js";

/**
 * illustrate-scene skill 契约测试（Phase 33-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 33 8 工具 allowlist
 * （7 只读 + generate_image_candidate action）、真实 loader 接受 pinned
 * manifest（fail-closed 通过后才存在 LoadedSkill）、registry-valid
 * fail-closed（未知工具/未声明权限/schema drift）、双 fixture 通过 schema、
 * schema-mismatch 负面用例、取消语义、无 ApprovalRequest/Publisher/published
 * 边界（Phase 33 唯一状态机 candidate → validated → proposal_ready 只由
 * 确定性 validator 推进）、26-06 normalization trail、IllustrationRevision
 * 信封字段（D-33-01..D-33-04）。不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/illustrate-scene/", import.meta.url);

// 返回 any：JSON.parse 的产物是动态 JSON；`as SkillManifest` / `Value.Check`
// 需要宽松类型（25.2-05 tsc 门禁修复，不改断言语义）。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 33-05 注册的 14 个域工具（域工具全集；25.2-02 7 个 + Phase 27 世界模型 5 个
 *  + Phase 30 get_visual_bible + Phase 33 generate_image_candidate）。 */
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
] as const;

/** Phase 33 编排 allowlist：7 个只读域工具 + 1 个候选生成 action 工具。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "get_timeline",
  "get_relationships",
  "get_clues",
  "get_narrative_memory",
  "generate_image_candidate",
] as const;

/** Phase 33 唯一声明的审批动作集合：**空**——Phase 33 无 ApprovalRequest /
 *  Publisher / published 状态（approval 与确定性 publication 属于 Phase 34）。 */
const DECLARED_APPROVAL_ACTIONS: readonly string[] = [];

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
 * approval_required_for 为空（Phase 33 无 ApprovalRequest / Publisher /
 * published）。返回错误列表。
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

describe("illustrate-scene skill package", () => {
  describe("真实 loader 接受 pinned manifest（33-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("illustrate-scene");
      expect(skill.name).toBe("illustrate-scene");
      expect(skill.version).toBe("1.0.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor).toEqual([]);
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
        expect.arrayContaining([
          "canon",
          "visual_bible",
          "key_scene",
          "scene_spec",
          "prompt_revision",
          "illustration",
        ]),
      );
      expect(skill.instructions.length).toBeGreaterThan(0);
      expect(typeof skill.validateInput).toBe("function");
      expect(typeof skill.validateOutput).toBe("function");
    });

    it("pinned manifest 的 input 通过真实 loader 编译的校验器", () => {
      const skill = loadSkill("illustrate-scene");
      const example = readSkillJson("examples/basic.json") as { input: any };
      expect(skill.validateInput(example.input)).toBe(true);
    });

    it("loadAllowlistedSkills 包含 illustrate-scene（ResourceLoader allowlist 注册）", () => {
      const names = loadAllowlistedSkills().map((s) => s.name);
      expect(names).toContain("illustrate-scene");
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
        "prompt_revision_id",
        "visual_bible_version_id",
        "scene_spec_revision_id",
        "source_snapshot_id",
        "job_key",
      ]) {
        expect(schema.required).toContain(field);
      }
      expect(schema.additionalProperties).toBe(false);
    });

    it("output.schema.json 是合法 JSON 且为 draft-07 对象 schema", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
      expect(schema.type).toBe("object");
      expect(schema.required).toContain("illustration_revision");
    });

    it("output.schema.json 物化 IllustrationRevision 信封（D-33-01..D-33-04）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("illustration_revision");
      expect(schema.properties.schema_version.const).toBe("illustration-revision.v1");
      for (const field of [
        "evidence_refs",
        "input_hash",
        "model_lineage",
        "source_versions",
        "producing_skill",
        "producing_skill_version",
        "skill_version_id",
        "illustration_revision",
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

    it("illustration_revision 负载声明完整 branch-aware 血缘（review_state 恒为 candidate）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const payload = schema.properties.illustration_revision;
      expect(payload.required).toEqual(
        expect.arrayContaining([
          "schema_version",
          "artifact_kind",
          "revision_key",
          "revision_number",
          "authority_space",
          "scene_spec_hash",
          "prompt_revision_hash",
          "visual_bible_revision_hash",
          "source_snapshot_id",
          "source_snapshot_hash",
          "cutoff_chapter",
          "provider",
          "provider_model",
          "config_hash",
          "generator_version",
          "rights_status",
          "consistency_verdict",
          "fixture_set_hash",
          "budget_settled_calls",
          "review_state",
        ]),
      );
      // Phase 33 唯一状态机：review_state 枚举含 candidate/validated/proposal_ready，
      // 但 finalize 写入时恒为 candidate（approval bypass → schema/服务端拒绝）。
      expect(payload.properties.review_state.enum).toEqual([
        "candidate",
        "validated",
        "proposal_ready",
      ]);
      expect(payload.properties.schema_version.const).toBe("illustration-revision.v1");
      expect(payload.properties.artifact_kind.const).toBe("illustration_revision");
      expect(payload.properties.authority_space.enum).toEqual([
        "original",
        "derivative",
      ]);
      expect(payload.properties.scene_spec_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(payload.properties.rights_status.enum).toEqual([
        "unreviewed",
        "cleared",
        "pending",
        "denied",
      ]);
      expect(payload.properties.consistency_verdict.enum).toEqual([
        "pass",
        "concern",
        "fail",
        "unavailable",
      ]);
    });

    it("input.schema.json 声明 Phase 33 输入锚（确定性插图服务按引用消费）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of [
        "novel_id",
        "branch",
        "fork",
        "scene_spec_revision_id",
        "prompt_revision_id",
        "visual_bible_version_id",
        "source_snapshot_id",
        "job_key",
        "provider",
        "model",
        "width",
        "height",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.prompt_revision_id.minimum).toBe(1);
      expect(schema.properties.visual_bible_version_id.minimum).toBe(1);
      expect(schema.properties.provider.enum).toEqual(["mock"]);
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("illustrate-scene");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 8 个 Phase 33 域工具（7 只读 + generate_image_candidate）", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      // 全部是注册域工具（loader 会校验 ⊆ DOMAIN_TOOL_NAMES）。
      for (const tool of manifest.allowed_tools) {
        expect(REGISTERED_DOMAIN_TOOLS).toContain(tool);
      }
    });

    it("allowed_tools 含候选生成 action generate_image_candidate", () => {
      expect(manifest.allowed_tools).toContain("generate_image_candidate");
      expect(manifest.allowed_tools).not.toContain("publish_illustration");
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 为空（Phase 33 无 ApprovalRequest / Publisher / published）", () => {
      expect(manifest.approval_required_for).toEqual([]);
      expect("publisher" in manifest).toBe(false);
      expect(Object.keys(manifest)).not.toContain("publish_action");
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

    it("read_permissions 声明 canon/visual_bible/key_scene/scene_spec/prompt_revision/illustration 只读", () => {
      expect(manifest.read_permissions).toEqual(
        expect.arrayContaining([
          "canon",
          "visual_bible",
          "key_scene",
          "scene_spec",
          "prompt_revision",
          "illustration",
        ]),
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
        allowed_tools: [...manifest.allowed_tools, "publish_illustration"],
      });
      expect(errors).toContain("unknown tools: publish_illustration");
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

    it("拒绝未声明的审批动作（Phase 33 无 ApprovalRequest/Publisher 授权）", () => {
      const errors = validateSkillContract({
        ...manifest,
        approval_required_for: ["illustration:publish"],
      });
      expect(errors).toContain("undeclared approval actions: illustration:publish");
    });

    it("schema drift：output type 是声明的 illustration_revision（真实 loader 编译通过）", () => {
      expect(() => loadSkill("illustrate-scene")).not.toThrow();
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.type.const).toBe("illustration_revision");
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, example.input)).toBe(true);
    });

    it("examples/basic.json expected_output（IllustrationRevision）通过 output.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("tests/basic.json input 通过 input.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, fixture.input)).toBe(true);
    });

    it("tests/basic.json expected_output（derivative IllustrationRevision）通过 output.schema 校验", () => {
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
          scene_spec_revision_id: 10,
          prompt_revision_id: 20,
          visual_bible_version_id: 30,
          source_snapshot_id: "ss-1",
          job_key: "ill-main-1",
        }),
      ).toBe(false);
    });

    it("input 缺 prompt_revision_id / job_key → 拒绝（服务端引用锚）", () => {
      const { prompt_revision_id, ...broken } = example.input;
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

    it("input 的 provider 非 mock → 拒绝（服务端未配置提供商 fail closed）", () => {
      expect(
        Value.Check(inputSchema, { ...example.input, provider: "openai" }),
      ).toBe(false);
    });

    it("output 的 type 非法（既非 illustration_revision）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, { ...example.expected_output, type: "story_arc" }),
      ).toBe(false);
    });

    it("output 的 schema_version 非法（schema drift）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          schema_version: "illustration-revision.v2",
        }),
      ).toBe(false);
    });

    it("output 的 status 非法 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, { ...example.expected_output, status: "bogus" }),
      ).toBe(false);
    });

    it("output 的 evidence_refs 为空数组 → 拒绝（leaf-evidence 资格门）", () => {
      expect(
        Value.Check(outputSchema, { ...example.expected_output, evidence_refs: [] }),
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

    it("output 的 illustration_revision.review_state 非 candidate 枚举（approval bypass）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          illustration_revision: {
            ...example.expected_output.illustration_revision,
            review_state: "published",
          },
        }),
      ).toBe(false);
    });

    it("output 的 illustration_revision 缺 scene_spec_hash → 拒绝（lineage 必须）", () => {
      const payload = example.expected_output.illustration_revision;
      const { scene_spec_hash, ...broken } = payload;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          illustration_revision: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 的 illustration_revision 缺 review_state → 拒绝（Phase 33 状态机必须）", () => {
      const payload = example.expected_output.illustration_revision;
      const { review_state, ...broken } = payload;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          illustration_revision: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 的 illustration_revision 缺 evidence 血缘（visual_bible_revision_hash）→ 拒绝", () => {
      const payload = example.expected_output.illustration_revision;
      const { visual_bible_revision_hash, ...broken } = payload;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          illustration_revision: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 缺 illustration_revision 负载 → 拒绝", () => {
      const { illustration_revision, ...broken } = example.expected_output;
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

  describe("取消与 Phase 33 边界（fail-closed 语义）", () => {
    it("SKILL.md 声明取消 → cancelled 且零 artifact/revision 写入", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("cancel_requested");
      expect(skill).toContain("cancelled");
      expect(skill).toContain("0 artifact 行");
      expect(skill).toContain("0 revision 行");
    });

    it("SKILL.md 声明无 ApprovalRequest / Publisher / published（Phase 33 边界）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "ApprovalRequest",
        "Publisher",
        "published",
        "绝不",
        "fail closed",
        "Phase 34",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 approval_required_for 为空（Phase 33 从不创建 ApprovalRequest）", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("approval_required_for");
      expect(skill).toContain("Phase 33 无 ApprovalRequest");
    });

    it("SKILL.md 声明唯一状态机 candidate → validated → proposal_ready 只由确定性 validator 推进", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "candidate → validated → proposal_ready",
        "确定性 validator",
        "仅前向",
        "review_state",
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

    it("SKILL.md 声明 proposal_ready handoff 只读（Phase 34 只接受 proposal_ready）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["proposal_ready", "只读", "Phase 34", "IllustrationAnchorProposal"]) {
        expect(skill).toContain(token);
      }
    });

    it("skill.yaml 声明 allowed_tools 含 generate_image_candidate 且无 publisher/approval 授权", () => {
      const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest & {
        publisher?: unknown;
        publish_action?: unknown;
      };
      expect(manifest.allowed_tools).toContain("generate_image_candidate");
      expect(manifest.approval_required_for).toEqual([]);
      expect(manifest.write_permissions).toEqual([]);
      expect("publisher" in manifest).toBe(false);
      expect(manifest.publish_action).toBeUndefined();
    });

    it("SKILL.md 声明 allowlist 外工具 → fail closed", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("allowlist 外");
    });
  });

  describe("candidate-only 域纪律（D-33-01..D-33-04）", () => {
    it("SKILL.md 声明生成候选绝不进入 reader/export / Canon", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["候选", "reader/export", "绝不", "candidate"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 identity/style consistency 是 review signal 不是 canon（D-33-04）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["review signal", "不是 canon", "D-33-04"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明确定性 validator 权威（budget/rights/fidelity/consistency gate）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["budget", "rights", "fidelity", "consistency", "validator"]) {
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
  });

  describe("Phase 33 normalization trail 正/负用例", () => {
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
            { path: "producing_skill", action: "alias", before: "skill_name", after: "illustrate-scene", reason: "declared alias" },
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
              { path: "illustration_revision", action: "hallucinate_fact", after: { x: 1 } },
            ],
          },
        }),
      ).toBe(false);
    });
  });
});
