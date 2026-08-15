import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  loadSkill,
  loadAllowlistedSkills,
} from "../../src/skills/loader.js";

/**
 * compile-scene-spec skill 契约测试（Phase 32-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 32 2 工具 allowlist、
 * 真实 loader 接受 pinned manifest（fail-closed 通过后才存在 LoadedSkill）、
 * registry-valid fail-closed（未知工具/未声明权限/schema drift）、双 fixture
 * 通过 schema、schema-mismatch 负面用例、取消语义、`scene_spec:approve`
 * 审批边界（candidate-only，只授权 Phase 33 消费；Agent 不能伪造批准）、
 * 26-06 normalization trail、SceneSpecArtifact/PromptArtifact 信封字段
 * （D-32-01..D-32-04）。不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/compile-scene-spec/", import.meta.url);

// 返回 any：JSON.parse 的产物是动态 JSON；`as SkillManifest` / `Value.Check`
// 需要宽松类型（25.2-05 tsc 门禁修复，不改断言语义）。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 32-05 注册的 13 个域工具（域工具全集；25.2-02 7 个 + Phase 27 世界模型 5 个 + Phase 30 get_visual_bible）。 */
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
] as const;

/** Phase 32 编排 allowlist：2 个只读域工具（Visual Bible + leaf evidence）。
 *  validated SceneCandidate / provider capabilities 由服务端确定性编译器按引用
 *  消费（candidate_set_id/candidate_key/visual_bible_version_id/source_snapshot_id）。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_visual_bible",
  "get_evidence_span",
] as const;

/** Phase 32 唯一声明的审批动作（D-32-04：用户审查/批准只授权 Phase 33 消费）。 */
const DECLARED_APPROVAL_ACTIONS = ["scene_spec:approve"] as const;

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
 * approval_required_for 只允许声明的 `scene_spec:approve`（未声明审批动作 →
 * 越权）。返回错误列表。
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
    (action) => !(DECLARED_APPROVAL_ACTIONS as readonly string[]).includes(action),
  );
  if (undeclared.length > 0) {
    errors.push(`undeclared approval actions: ${undeclared.join(", ")}`);
  }
  return errors;
}

describe("compile-scene-spec skill package", () => {
  describe("真实 loader 接受 pinned manifest（32-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("compile-scene-spec");
      expect(skill.name).toBe("compile-scene-spec");
      expect(skill.version).toBe("1.0.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor).toEqual(["scene_spec:approve"]);
      expect(skill.forbiddenSpaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "visual_bible:write",
          "key_scene:write",
          "scene_spec:write",
        ]),
      );
      expect(skill.readPermissions).toEqual(
        expect.arrayContaining(["canon", "visual_bible", "key_scene", "scene_spec"]),
      );
      expect(skill.instructions.length).toBeGreaterThan(0);
      expect(typeof skill.validateInput).toBe("function");
      expect(typeof skill.validateOutput).toBe("function");
    });

    it("pinned manifest 的 input 通过真实 loader 编译的校验器", () => {
      const skill = loadSkill("compile-scene-spec");
      const example = readSkillJson("examples/basic.json") as { input: any };
      expect(skill.validateInput(example.input)).toBe(true);
    });

    it("loadAllowlistedSkills 包含 compile-scene-spec（ResourceLoader allowlist 注册）", () => {
      const names = loadAllowlistedSkills().map((s) => s.name);
      expect(names).toContain("compile-scene-spec");
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
        "spec_key",
        "candidate_set_id",
        "candidate_key",
        "visual_bible_version_id",
        "source_snapshot_id",
      ]) {
        expect(schema.required).toContain(field);
      }
      expect(schema.additionalProperties).toBe(false);
    });

    it("output.schema.json 是合法 JSON 且为 draft-07 对象 schema", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
      expect(schema.type).toBe("object");
      expect(Array.isArray(schema.oneOf)).toBe(true);
    });

    it("output.schema.json 物化 SceneSpecArtifact 信封（D-32-01..D-32-04）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const artifact = schema.definitions.scene_spec_artifact;
      expect(artifact.properties.type.const).toBe("scene_spec");
      expect(artifact.properties.schema_version.const).toBe("scene-spec.v1");
      for (const field of [
        "evidence_refs",
        "input_hash",
        "model_lineage",
        "source_versions",
        "producing_skill",
        "producing_skill_version",
        "skill_version_id",
        "scene_spec",
        "tool_runs",
        "normalization",
        "status",
        "parent_revision",
      ]) {
        expect(artifact.properties).toHaveProperty(field);
      }
      expect(artifact.properties.status.enum).toEqual([
        "candidate",
        "validated",
        "approved",
        "published",
        "rejected",
      ]);
    });

    it("output.schema.json 物化 PromptArtifact 信封（type=prompt）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const artifact = schema.definitions.prompt_artifact;
      expect(artifact.properties.type.const).toBe("prompt");
      expect(artifact.properties.schema_version.const).toBe("prompt-revision.v1");
      for (const field of [
        "prompt_revision",
        "scene_spec",
        "evidence_refs",
        "input_hash",
        "tool_runs",
        "normalization",
      ]) {
        expect(artifact.properties).toHaveProperty(field);
      }
      expect(artifact.required).toContain("prompt_revision");
      expect(artifact.required).toContain("scene_spec");
    });

    it("scene_spec 负载声明完整 SceneSpecContract（review_state 恒为 candidate）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const payload = schema.definitions.scene_spec_payload;
      expect(payload.required).toEqual(
        expect.arrayContaining([
          "schema_version",
          "artifact_kind",
          "owner_id",
          "novel_id",
          "spec_key",
          "revision_number",
          "scene_candidate_hash",
          "visual_bible_revision_hash",
          "source_snapshot_id",
          "source_snapshot_hash",
          "cutoff_chapter",
          "schema_hash",
          "compiler_id",
          "compiler_version",
          "policy_hash",
          "content_hash",
          "details",
          "negative_constraints",
          "uncertainties",
          "review_state",
        ]),
      );
      // D-32-04：approval 是服务端显式状态迁移（只授权 Phase 33 消费）——Agent
      // 声称任何非 candidate review_state（approval bypass）→ schema 拒绝。
      expect(payload.properties.review_state.const).toBe("candidate");
      expect(payload.properties.schema_version.const).toBe("scene-spec.v1");
      expect(payload.properties.artifact_kind.const).toBe("scene_spec");
      expect(payload.properties.content_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(payload.properties.details.minItems).toBe(1);
    });

    it("prompt_revision 负载声明完整 PromptRevisionContract（adapter lineage + hash 重放）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const payload = schema.definitions.prompt_revision_payload;
      for (const field of [
        "prompt_key",
        "scene_spec_hash",
        "visual_bible_revision_hash",
        "source_snapshot_id",
        "source_snapshot_hash",
        "cutoff_chapter",
        "schema_hash",
        "prompt_schema_hash",
        "compiler_version",
        "adapter_id",
        "adapter_version",
        "config_hash",
        "input_hash",
        "prompt_hash",
        "sections",
        "negative_constraints",
        "uncertainties",
        "prompt_text",
        "review_state",
      ]) {
        expect(payload.properties).toHaveProperty(field);
      }
      expect(payload.properties.review_state.const).toBe("candidate");
      expect(payload.properties.schema_version.const).toBe("prompt-revision.v1");
      expect(payload.properties.artifact_kind.const).toBe("prompt_revision");
      expect(payload.properties.input_hash.pattern).toBe("^[0-9a-f]{64}$");
    });

    it("input.schema.json 声明 Phase 32 输入锚（确定性编译器按引用消费）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of [
        "novel_id",
        "branch",
        "spec_key",
        "candidate_set_id",
        "candidate_key",
        "visual_bible_version_id",
        "source_snapshot_id",
        "revision_number",
        "policy_hash",
        "config_hash",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.candidate_set_id.minimum).toBe(1);
      expect(schema.properties.visual_bible_version_id.minimum).toBe(1);
      expect(schema.properties.policy_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("compile-scene-spec");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 2 个 Phase 32 只读域工具", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      // 全部是注册域工具（loader 会校验 ⊆ DOMAIN_TOOL_NAMES）。
      for (const tool of manifest.allowed_tools) {
        expect(REGISTERED_DOMAIN_TOOLS).toContain(tool);
      }
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 声明唯一审批动作 scene_spec:approve（只授权 Phase 33 消费）", () => {
      expect(manifest.approval_required_for).toEqual(["scene_spec:approve"]);
    });

    it("forbidden_spaces 覆盖 canon:original 与 scene_spec:write", () => {
      expect(manifest.forbidden_spaces).toEqual(
        expect.arrayContaining([
          "canon:original",
          "visual_bible:write",
          "key_scene:write",
          "scene_spec:write",
        ]),
      );
    });

    it("read_permissions 声明 canon/visual_bible/key_scene/scene_spec 只读", () => {
      expect(manifest.read_permissions).toEqual(
        expect.arrayContaining(["canon", "visual_bible", "key_scene", "scene_spec"]),
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
        allowed_tools: [...manifest.allowed_tools, "delete_scene_spec"],
      });
      expect(errors).toContain("unknown tools: delete_scene_spec");
    });

    it("拒绝空 allowed_tools", () => {
      const errors = validateSkillContract({ ...manifest, allowed_tools: [] });
      expect(errors).toContain("allowed_tools must be non-empty");
    });

    it("拒绝非空 write_permissions（Agent 零域写入）", () => {
      const errors = validateSkillContract({
        ...manifest,
        write_permissions: ["scene_spec"],
      });
      expect(errors).toContain("agent must declare empty write_permissions");
    });

    it("拒绝未声明的审批动作（越权批准/发布/写回 Canon）", () => {
      const errors = validateSkillContract({
        ...manifest,
        approval_required_for: ["canon:promote"],
      });
      expect(errors).toContain("undeclared approval actions: canon:promote");
    });

    it("schema drift：output type 是声明的 scene_spec / prompt（真实 loader 编译通过）", () => {
      expect(() => loadSkill("compile-scene-spec")).not.toThrow();
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.definitions.scene_spec_artifact.properties.type.const).toBe("scene_spec");
      expect(schema.definitions.prompt_artifact.properties.type.const).toBe("prompt");
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, example.input)).toBe(true);
    });

    it("examples/basic.json expected_output（SceneSpecArtifact）通过 output.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("tests/basic.json input 通过 input.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, fixture.input)).toBe(true);
    });

    it("tests/basic.json expected_output（PromptArtifact）通过 output.schema 校验", () => {
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
          spec_key: "spec-main",
          candidate_set_id: 10,
          candidate_key: "ks-main-0",
          visual_bible_version_id: 20,
          source_snapshot_id: "ss-1",
        }),
      ).toBe(false);
    });

    it("input 缺 candidate_set_id / visual_bible_version_id → 拒绝（服务端引用锚）", () => {
      const { candidate_set_id, ...broken } = example.input;
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

    it("output 的 type 非法（既非 scene_spec 也非 prompt）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, { ...example.expected_output, type: "story_arc" }),
      ).toBe(false);
    });

    it("output 的 schema_version 非法（schema drift）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          schema_version: "scene-spec.v2",
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

    it("output 的 scene_spec.review_state 非 candidate（approval bypass）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          scene_spec: {
            ...example.expected_output.scene_spec,
            review_state: "approved",
          },
        }),
      ).toBe(false);
    });

    it("output 的 scene_spec 缺 visual_bible_revision_hash → 拒绝（lineage 必须）", () => {
      const spec = example.expected_output.scene_spec;
      const { visual_bible_revision_hash, ...broken } = spec;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          scene_spec: { ...broken },
        }),
      ).toBe(false);
    });

    it("output 的 scene_spec 缺 details → 拒绝（candidate 细节必须）", () => {
      const spec = example.expected_output.scene_spec;
      const { details, ...broken } = spec;
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          scene_spec: { ...broken },
        }),
      ).toBe(false);
    });

    it("PromptArtifact 缺 prompt_revision 负载 → 拒绝", () => {
      const promptFixture = readSkillJson("tests/basic.json") as {
        expected_output: any;
      };
      const { prompt_revision, ...broken } = promptFixture.expected_output;
      expect(Value.Check(outputSchema, { ...broken })).toBe(false);
    });

    it("PromptArtifact 的 prompt_revision.review_state 非 candidate → 拒绝", () => {
      const promptFixture = readSkillJson("tests/basic.json") as {
        expected_output: any;
      };
      expect(
        Value.Check(outputSchema, {
          ...promptFixture.expected_output,
          prompt_revision: {
            ...promptFixture.expected_output.prompt_revision,
            review_state: "approved",
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

  describe("取消与审批边界（fail-closed 语义）", () => {
    it("SKILL.md 声明取消 → cancelled 且零 artifact/revision 写入", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("cancel_requested");
      expect(skill).toContain("cancelled");
      expect(skill).toContain("0 artifact 行");
      expect(skill).toContain("0 revision 行");
    });

    it("SKILL.md 声明 scene_spec:approve 审批边界（Agent 不能伪造批准）", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("scene_spec:approve");
      expect(skill).toContain("暂停等待审批");
      expect(skill).toContain("不能");
      expect(skill).toContain("伪造批准");
      expect(skill).toContain("fail closed");
    });

    it("SKILL.md 声明审批只授权 Phase 33 消费，无 Publisher 越权 / 无 promotion / 无 Canon 写回", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "Phase 33 消费",
        "Publisher",
        "promotion",
        "写回",
        "绝不",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("skill.yaml 声明审批动作只有 scene_spec:approve 且不授权 Agent 发布", () => {
      const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest & {
        publisher?: unknown;
      };
      expect(manifest.approval_required_for).toEqual(["scene_spec:approve"]);
      expect(manifest.write_permissions).toEqual([]);
      expect("publisher" in manifest).toBe(false);
      expect(Object.keys(manifest)).not.toContain("publish_action");
    });

    it("SKILL.md 声明 wrong owner/version/cutoff/schema drift/stale revision → 稳定 blocked/cancelled 零写入", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "wrong owner",
        "wrong skill_version",
        "wrong cutoff",
        "schema drift",
        "stale visual_bible_revision_hash",
        "零写入",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 allowlist 外工具 → fail closed", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("allowlist 外");
    });
  });

  describe("candidate-only 域纪律（D-32-01..D-32-04）", () => {
    it("SKILL.md 声明 spec/prompt 绝不进入 Canon / Visual Bible / active reader state", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["Canon", "Visual Bible", "active reader", "candidate"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 review_state 恒为 candidate、approval 是 append-only 服务端迁移", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["candidate", "append-only", "approved_only", "review_state"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明确定性 Canon/Visual Bible 一致性与未支持细节 validator 权威", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["Canon/Visual Bible 一致性", "未支持细节", "validator"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 prompt 字符串永远不是权威（D-32-01）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["不是", "权威", "provider-neutral", "不调用"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 validated SceneCandidate 与 provider capabilities 按引用服务端消费", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["candidate_set_id", "candidate_key", "visual_bible_version_id", "source_snapshot_id"]) {
        expect(skill).toContain(token);
      }
    });
  });

  describe("Phase 32 normalization trail 正/负用例", () => {
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
            { path: "producing_skill", action: "alias", before: "skill_name", after: "compile-scene-spec", reason: "declared alias" },
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
              { path: "scene_spec", action: "hallucinate_fact", after: { x: 1 } },
            ],
          },
        }),
      ).toBe(false);
    });
  });
});
