import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  normalizeStructuredOutput,
  type NormalizeContract,
} from "../../src/structured-output/normalizer.js";
import {
  assertValidStructuredOutput,
  validateNormalizedOutput,
  StructuredOutputBlockedError,
} from "../../src/structured-output/validator.js";
import { loadSkill } from "../../src/skills/loader.js";

/**
 * build-story-arc skill 契约测试（Phase 28-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 28 8 工具 allowlist、
 * 真实 loader 接受 pinned manifest、registry-valid fail-closed（未知工具/未声明
 * 权限/schema drift）、双 fixture 通过 schema、schema-mismatch 负面用例、取消
 * 语义、候选纪律（D-09：Outline/Mainline candidate-only，绝不进入 Canon、
 * 保留不确定性/source lineage、gaps/overlaps 如实呈现）。不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/build-story-arc/", import.meta.url);

function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 28-05 注册的 12 个域工具（域工具全集）。 */
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
] as const;

/** Phase 28 编排 allowlist：8 个只读域工具。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_chapter",
  "get_evidence_span",
  "get_events",
  "get_character_state",
  "get_relationships",
  "get_clues",
  "get_world_rules",
  "get_narrative_memory",
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

/** 极简 YAML 解析器（与 analyze-chapter 测试同源，支持本项目 skill.yaml 子集）。 */
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
 * Skill-local registry-valid 校验（模拟 loader 的 fail-closed 门禁语义）：
 * allowed_tools ⊆ 注册工具集；write_permissions / approval_required_for 为空。
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
    errors.push("candidate-only skill must declare empty write_permissions");
  }
  if (m.approval_required_for.length > 0) {
    errors.push("candidate-only skill must declare empty approval_required_for");
  }
  return errors;
}

describe("build-story-arc skill package", () => {
  describe("真实 loader 接受 pinned manifest（28-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("build-story-arc");
      expect(skill.name).toBe("build-story-arc");
      expect(skill.version).toBe("1.0.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor).toEqual([]);
      expect(skill.forbiddenSpaces).toEqual(
        expect.arrayContaining(["canon:original", "derivative:write"]),
      );
      expect(skill.readPermissions).toEqual(
        expect.arrayContaining(["canon", "derivative", "narrative_memory"]),
      );
      expect(skill.instructions.length).toBeGreaterThan(0);
      expect(typeof skill.validateInput).toBe("function");
      expect(typeof skill.validateOutput).toBe("function");
    });

    it("pinned manifest 的 input 通过真实 loader 编译的校验器", () => {
      const skill = loadSkill("build-story-arc");
      const example = readSkillJson("examples/basic.json") as { input: any };
      expect(skill.validateInput(example.input)).toBe(true);
    });
  });

  describe("JSON Schema 文件有效性", () => {
    it("input.schema.json 是合法 JSON 且为 draft-07 对象 schema", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
      expect(schema.type).toBe("object");
      expect(Array.isArray(schema.required)).toBe(true);
      expect(schema.required).toContain("novel_id");
      expect(schema.additionalProperties).toBe(false);
    });

    it("output.schema.json 是合法 JSON 且为 draft-07 对象 schema", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
      expect(schema.type).toBe("object");
      expect(Array.isArray(schema.required)).toBe(true);
      expect(schema.additionalProperties).toBe(false);
    });

    it("output.schema.json 声明 StoryArcArtifact 信封字段", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      for (const field of [
        "evidence_refs",
        "input_hash",
        "schema_version",
        "status",
        "parent_revision",
        "model_lineage",
        "source_versions",
        "producing_skill",
        "producing_skill_version",
        "skill_version_id",
        "outline_candidate",
        "mainline_candidate",
        "tool_runs",
        "normalization",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.type.const).toBe("story_arc");
      expect(schema.properties.schema_version.const).toBe("story-arc.v1");
      expect(schema.properties.status.enum).toEqual([
        "candidate",
        "validated",
        "approved",
        "published",
        "rejected",
      ]);
    });

    it("outline_candidate 声明候选纪律：schema_version / candidate_status=candidate / lineage / checksum", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const outline = schema.properties.outline_candidate;
      expect(outline.type).toBe("object");
      expect(outline.properties.schema_version.const).toBe("outline-candidate-artifact.v1");
      expect(outline.properties.candidate_status.const).toBe("candidate");
      for (const field of [
        "policy_version",
        "owner_id",
        "novel_id",
        "version_id",
        "source_snapshot_hash",
        "hierarchy_build_id",
        "hierarchy_checksum",
        "input_hash",
        "chapter_min",
        "chapter_max",
        "arcs",
        "covered_ranges",
        "candidate_status",
        "lineage",
        "checksum",
      ]) {
        expect(outline.required).toContain(field);
      }
      // 弧线候选：不确定性 + evidence lineage + 连续 span。
      const arc = outline.properties.arcs.items;
      expect(arc.required).toEqual(
        expect.arrayContaining([
          "stage_key",
          "node_kind",
          "chapter_start",
          "chapter_end",
          "chapter_numbers",
          "coverage",
          "uncertainty",
          "confidence",
          "input_hash",
        ]),
      );
      expect(arc.properties.node_kind.const).toBe("story_arc");
      expect(arc.properties.uncertainty.enum).toEqual([
        "certain",
        "likely",
        "uncertain",
        "unknown",
      ]);
    });

    it("mainline_candidate 声明候选纪律：schema_version / candidate_status=candidate / volumes / global_projection", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const mainline = schema.properties.mainline_candidate;
      expect(mainline.type).toBe("object");
      expect(mainline.properties.schema_version.const).toBe("mainline-candidate-artifact.v1");
      expect(mainline.properties.candidate_status.const).toBe("candidate");
      for (const field of [
        "volumes",
        "global_projection",
        "covered_ranges",
        "candidate_status",
        "lineage",
        "checksum",
      ]) {
        expect(mainline.required).toContain(field);
      }
      expect(mainline.properties.global_projection.properties.node_kind.const).toBe(
        "global_story",
      );
      expect(mainline.properties.global_projection.properties.stage_key.const).toBe(
        "global_story:book",
      );
    });

    it("output.schema.json 声明 26-06 normalization trail 形状", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const normalization = schema.properties.normalization;
      expect(normalization.required).toEqual([
        "raw_hash",
        "repaired_hash",
        "normalization_actions",
        "warnings",
      ]);
    });

    it("input.schema.json 声明 Phase 28 输入锚（novel_id/chapter_range/branch/cutoff/source_snapshot）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of ["novel_id", "chapter_range", "branch", "cutoff", "source_snapshot"]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.chapter_range.required).toEqual([
        "chapter_start",
        "chapter_end",
      ]);
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("build-story-arc");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 8 个 Phase 28 工具（含 get_narrative_memory）", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(manifest.allowed_tools).toContain("get_narrative_memory");
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 为空数组（零审批动作）", () => {
      expect(manifest.approval_required_for).toEqual([]);
    });

    it("forbidden_spaces 覆盖 canon:original 与 derivative:write", () => {
      expect(manifest.forbidden_spaces).toEqual(
        expect.arrayContaining(["canon:original", "derivative:write"]),
      );
    });

    it("budget 声明 per-run 上限", () => {
      expect(manifest.budget.max_calls).toBe(40);
      expect(manifest.budget.max_input_tokens).toBe(60000);
      expect(manifest.budget.max_output_tokens).toBe(12000);
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
        allowed_tools: [...manifest.allowed_tools, "delete_novel"],
      });
      expect(errors).toContain("unknown tools: delete_novel");
    });

    it("拒绝非空 write_permissions（Agent 零域写入）", () => {
      const errors = validateSkillContract({
        ...manifest,
        write_permissions: ["canon"],
      });
      expect(errors).toContain(
        "candidate-only skill must declare empty write_permissions",
      );
    });

    it("拒绝非空 approval_required_for（candidate-only 零审批）", () => {
      const errors = validateSkillContract({
        ...manifest,
        approval_required_for: ["canon:original"],
      });
      expect(errors).toContain(
        "candidate-only skill must declare empty approval_required_for",
      );
    });

    it("schema drift：output schema_version 是声明的 story-arc.v1（真实 loader 编译通过）", () => {
      expect(() => loadSkill("build-story-arc")).not.toThrow();
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      expect(schema.properties.schema_version.const).toBe("story-arc.v1");
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, example.input)).toBe(true);
    });

    it("examples/basic.json expected_output 通过 output.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        expected_output: unknown;
      };
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("tests/basic.json input 通过 input.schema 校验", () => {
      const fixture = readSkillJson("tests/basic.json") as { input: unknown };
      expect(Value.Check(inputSchema, fixture.input)).toBe(true);
    });

    it("tests/basic.json expected_output 通过 output.schema 校验", () => {
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

    it("input 多余未知字段（additionalProperties:false）→ 拒绝", () => {
      expect(
        Value.Check(inputSchema, { novel_id: 1, hacked: true }),
      ).toBe(false);
    });

    it("input 的 chapter_range 缺 chapter_end → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          novel_id: 1,
          chapter_range: { chapter_start: 1 },
        }),
      ).toBe(false);
    });

    it("output 的 type 非法 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, { ...example.expected_output, type: "cited_answer" }),
      ).toBe(false);
    });

    it("output 的 schema_version 非法（schema drift）→ 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          schema_version: "story-arc.v2",
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

    it("output 的 outline_candidate.candidate_status 非 candidate → 拒绝（绝不进入 Canon）", () => {
      const outline = {
        ...example.expected_output.outline_candidate,
        candidate_status: "published",
      };
      expect(
        Value.Check(outputSchema, { ...example.expected_output, outline_candidate: outline }),
      ).toBe(false);
    });

    it("output 的 mainline_candidate.candidate_status 非 candidate → 拒绝（绝不进入 Canon）", () => {
      const mainline = {
        ...example.expected_output.mainline_candidate,
        candidate_status: "canon",
      };
      expect(
        Value.Check(outputSchema, { ...example.expected_output, mainline_candidate: mainline }),
      ).toBe(false);
    });

    it("output 缺 mainline_candidate → 拒绝（官方输出必须物化 outline + mainline 候选）", () => {
      const { mainline_candidate: _drop, ...withoutMainline } = example.expected_output;
      expect(Value.Check(outputSchema, withoutMainline)).toBe(false);
    });
  });

  describe("取消与候选纪律（fail-closed 语义）", () => {
    it("SKILL.md 声明取消 → cancelled 且零 artifact/revision 写入", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("cancel_requested");
      expect(skill).toContain("cancelled");
      expect(skill).toContain("0 artifact 行");
      expect(skill).toContain("0 revision 行");
    });

    it("SKILL.md 声明无 ApprovalRequest / 无 Publisher / 无 promotion / 无 Canon 写入", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["ApprovalRequest", "Publisher", "promotion", "Canon", "fail closed"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 Outline/Mainline candidate-only：绝不因生成进入 Canon（D-09）", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("candidate-only");
      expect(skill).toContain("绝不进入 Canon");
      expect(skill).toContain("candidate_status");
    });

    it("SKILL.md 声明 gaps/overlaps/不确定性如实保留，绝不伪装为完整事实", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("gap");
      expect(skill).toContain("不确定性");
      expect(skill).toContain("覆盖");
    });

    it("SKILL.md 声明情绪记忆 out of scope", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("情绪记忆");
      expect(skill).toContain("out of scope");
    });

    it("skill.yaml 声明审批动作为空且不授权 Agent 发布（write_permissions 为空）", () => {
      const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest & {
        publisher?: unknown;
      };
      expect(manifest.approval_required_for).toEqual([]);
      expect(manifest.write_permissions).toEqual([]);
      expect("publisher" in manifest).toBe(false);
      expect(Object.keys(manifest)).not.toContain("publish_action");
    });

    it("SKILL.md 声明 wrong owner/version/cutoff/schema drift → 稳定 blocked/cancelled 零写入", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["wrong owner", "wrong skill_version", "wrong cutoff", "schema drift", "零写入"]) {
        expect(skill).toContain(token);
      }
    });
  });

  describe("Phase 28 normalization trail 正/负用例", () => {
    const outputSchema = readSkillJson("output.schema.json");
    const example = readSkillJson("examples/basic.json") as { expected_output: any };
    const trail = example.expected_output.normalization as Record<string, any>;

    it("合法 normalization trail（noop 修复）→ 通过", () => {
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("normalization 缺 raw_hash → 拒绝", () => {
      const { raw_hash, ...broken } = trail;
      expect(
        Value.Check(outputSchema, { ...example.expected_output, normalization: { ...broken } }),
      ).toBe(false);
    });

    it("normalization_actions 项 action 非声明修复种类 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          normalization: {
            ...trail,
            normalization_actions: [
              { path: "outline_candidate", action: "promote_to_canon", after: { x: 1 } },
            ],
          },
        }),
      ).toBe(false);
    });
  });

  describe("共享 26-06 normalizer/validator 消费（无本地修复路径）", () => {
    const outputSchema = readSkillJson("output.schema.json");
    const example = readSkillJson("examples/basic.json") as { expected_output: any };

    /** 代表 story_arc 信封的声明式修复契约（与 26-06 同源）。 */
    const contract: NormalizeContract = {
      aliases: {
        producing_skill: ["skill_name"],
        producing_skill_version: ["skill_version"],
      },
      lineageFields: {
        owner_id: "ownerId",
        novel_id: "novelId",
        skill_version_id: "skillVersionId",
        model_lineage: "modelLineage",
        source_versions: "sourceVersions",
        input_hash: "inputHash",
        branch: "branch",
        evidence_refs: "evidenceRefs",
      },
      requiredFields: [
        "type",
        "schema_version",
        "producing_skill",
        "producing_skill_version",
        "owner_id",
        "novel_id",
        "skill_version_id",
        "model_lineage",
        "source_versions",
        "input_hash",
        "evidence_refs",
        "outline_candidate",
        "mainline_candidate",
        "tool_runs",
        "status",
      ],
    };

    const lineage = (): Record<string, unknown> => ({
      ownerId: 1,
      novelId: 1,
      skillVersionId: 1,
      modelLineage: { provider: "fixture", model: "stub-model", revision: "stub-1" },
      sourceVersions: { novel: "v1", narrative_memory: "v1" },
      inputHash: "a".repeat(64),
      branch: null,
      evidenceRefs: ["evidence:1"],
    });

    function rawModelOutput(): Record<string, unknown> {
      return {
        type: "story_arc",
        schema_version: "story-arc.v1",
        skill_name: "build-story-arc",
        skill_version: "1.0.0",
        outline_candidate: example.expected_output.outline_candidate,
        mainline_candidate: example.expected_output.mainline_candidate,
        tool_runs: [{ tool_name: "get_narrative_memory", calls: 1 }],
        status: "candidate",
      };
    }

    it("修复后 repaired payload 通过 skill output schema + 严格 validator", () => {
      const result = normalizeStructuredOutput(rawModelOutput(), contract, lineage());
      expect(result.status).toBe("ok");
      const outcome = validateNormalizedOutput(result, {
        schema: outputSchema,
        allowedEvidenceRefs: ["evidence:1", "evidence:2", "evidence:3"],
        requiredProtectedFields: ["owner_id", "novel_id", "evidence_refs", "input_hash"],
        requireEvidenceRefs: true,
      });
      expect(outcome.status).toBe("valid");
      expect(outcome.verified_raw_hash).toBe(result.raw_hash);
      expect(outcome.verified_repaired_hash).toBe(result.repaired_hash);
    });

    it("unsafe-repair：修复路径触及受保护字段 → contract-invalid → blocked", () => {
      const unsafe: NormalizeContract = {
        ...contract,
        aliases: { evidence_refs: ["refs"] }, // 受保护字段 alias 禁止
      };
      const result = normalizeStructuredOutput(rawModelOutput(), unsafe, lineage());
      expect(result.status).toBe("blocked");
      expect(result.blocked_reason).toContain("contract-invalid");
      expect(result.repaired).toBeNull();
    });

    it("strict-validator 失败：repaired payload 被篡改（stale repaired_hash）→ blocked", () => {
      const result = normalizeStructuredOutput(rawModelOutput(), contract, lineage());
      expect(result.status).toBe("ok");
      (result.repaired as Record<string, unknown>).status = "published";
      const outcome = validateNormalizedOutput(result, { schema: outputSchema });
      expect(outcome.status).toBe("blocked");
      expect(outcome.errors.join(";")).toContain("stale repaired_hash");
    });

    it("assertValidStructuredOutput 抛 StructuredOutputBlockedError（agent loop fail-closed）", () => {
      const result = normalizeStructuredOutput(rawModelOutput(), contract, lineage());
      expect(result.status).toBe("ok");
      (result.repaired as Record<string, unknown>).status = "approved";
      expect(() =>
        assertValidStructuredOutput(result, { schema: outputSchema }),
      ).toThrowError(StructuredOutputBlockedError);
    });
  });
});
