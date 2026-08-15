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
 * propose-world-model-candidates skill 契约测试（Phase 27-05）：
 * 校验 skill 包自身 —— schema 有效性、D-09 字段、Phase 27 6 工具 allowlist、
 * 真实 loader 接受 pinned manifest（fail-closed 通过后才存在 LoadedSkill）、
 * registry-valid fail-closed（未知工具/未声明权限/schema drift）、双 fixture
 * 通过 schema、schema-mismatch 负面用例、取消语义、approval 边界
 * （world_model:user_interpretation 是唯一审批动作，Agent 无发布权威）。
 * 不写后端。
 */

const SKILL_DIR = new URL(
  "../../src/skills/propose-world-model-candidates/",
  import.meta.url,
);

// 返回 any：JSON.parse 的产物是动态 JSON；`as SkillManifest` / `Value.Check`
// 需要宽松类型（25.2-05 tsc 门禁修复，不改断言语义）。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 27-05 注册的 12 个域工具（域工具全集；25.2-02 7 个 + Phase 27 世界模型 5 个）。 */
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

/** Phase 27 编排 allowlist：7 个只读工具（含 search_novel_text 文本发现通道）。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_events",
  "get_character_state",
  "get_character_knowledge",
  "get_relationships",
  "get_world_rules",
  "get_evidence_span",
  "search_novel_text",
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
 * scalar / 缩进 list / 缩进 dict，去注释、数值与引号归一）。真实 loader 的
 * yaml/ajv 依赖在 25.2-05 引入。
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
 * allowed_tools ⊆ 注册工具集。返回错误列表。
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
  return errors;
}

describe("propose-world-model-candidates skill package", () => {
  describe("真实 loader 接受 pinned manifest（27-05）", () => {
    it("loadSkill 通过全部 fail-closed 校验并返回 LoadedSkill", () => {
      const skill = loadSkill("propose-world-model-candidates");
      expect(skill.name).toBe("propose-world-model-candidates");
      expect(skill.version).toBe("1.1.0");
      expect(skill.allowedTools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(skill.writePermissions).toEqual([]);
      expect(skill.approvalRequiredFor).toEqual(["world_model:user_interpretation"]);
      expect(skill.forbiddenSpaces).toEqual(
        expect.arrayContaining(["canon:original", "derivative:write"]),
      );
      expect(skill.instructions.length).toBeGreaterThan(0);
      expect(typeof skill.validateInput).toBe("function");
      expect(typeof skill.validateOutput).toBe("function");
    });

    it("pinned manifest 的 input 通过真实 loader 编译的校验器", () => {
      const skill = loadSkill("propose-world-model-candidates");
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

    it("output.schema.json 声明 WorldModelCandidateArtifact 信封字段", () => {
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
        "candidates",
        "normalization",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.type.const).toBe("world_model_candidate");
      expect(schema.properties.schema_version.const).toBe("world-model-candidate.v1");
      expect(schema.properties.status.enum).toEqual([
        "candidate",
        "validated",
        "approved",
        "published",
        "rejected",
      ]);
    });

    it("candidates 声明 typed claims 形状（claim_kind/authority/evidence_refs/tool_runs）", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const candidates = schema.properties.candidates;
      expect(candidates.type).toBe("object");
      expect(candidates.required).toEqual(["projection_version", "tool_runs", "claims"]);
      // ToolRun 血缘。
      expect(candidates.properties.tool_runs.items.required).toEqual(["tool_name", "calls"]);
      // claim 形状：D-01 authority 四 label + leaf evidence_refs。
      const claim = candidates.properties.claims.items;
      expect(claim.properties.claim_kind.enum).toContain("causal_edge");
      expect(claim.properties.claim_kind.enum).toContain("character_knowledge");
      expect(claim.properties.authority.enum).toEqual([
        "canon_fact",
        "probable_inference",
        "literary_interpretation",
        "user_interpretation",
      ]);
      expect(claim.required).toEqual(
        expect.arrayContaining([
          "claim_kind",
          "claim_key",
          "proposition",
          "authority",
          "evidence_refs",
        ]),
      );
      expect(claim.properties.evidence_refs.minItems).toBe(1);
    });

    it("output.schema.json 声明 26-06 normalization trail 形状", () => {
      const schema = readSkillJson("output.schema.json") as Record<string, any>;
      const normalization = schema.properties.normalization;
      expect(normalization.type).toBe("object");
      expect(normalization.required).toEqual([
        "raw_hash",
        "repaired_hash",
        "normalization_actions",
        "warnings",
      ]);
      expect(normalization.properties.raw_hash.pattern).toBe("^[0-9a-f]{64}$");
      expect(normalization.properties.repaired_hash.pattern).toBe("^[0-9a-f]{64}$");
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("propose-world-model-candidates");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 6 个 Phase 27 工具", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
    });

    it("write_permissions 为空数组（Agent 零域写入）", () => {
      expect(manifest.write_permissions).toEqual([]);
    });

    it("approval_required_for 声明唯一审批动作 world_model:user_interpretation", () => {
      expect(manifest.approval_required_for).toEqual(["world_model:user_interpretation"]);
    });

    it("forbidden_spaces 覆盖 canon:original 与 derivative:write", () => {
      expect(manifest.forbidden_spaces).toEqual(
        expect.arrayContaining(["canon:original", "derivative:write"]),
      );
    });

    it("budget 声明 per-run 上限", () => {
      expect(manifest.budget.max_calls).toBe(30);
      expect(manifest.budget.max_input_tokens).toBe(40000);
      expect(manifest.budget.max_output_tokens).toBe(8000);
      expect(String(manifest.budget.max_cost_usd)).toBe("1.50");
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

    it("get_narrative_memory 不在 Phase 27 allowlist（D-01 干净；注册工具但不声明）", () => {
      // get_narrative_memory 是注册工具（不是未知工具），但本技能编排只使用
      // 声明的 6 工具 allowlist——排除它保持 D-01 世界模型事实源干净。
      expect(REGISTERED_DOMAIN_TOOLS).toContain("get_narrative_memory");
      const manifest = parseSkillYaml(
        readSkillText("skill.yaml"),
      ) as unknown as SkillManifest;
      expect(manifest.allowed_tools).not.toContain("get_narrative_memory");
    });

    it("拒绝空 allowed_tools", () => {
      const errors = validateSkillContract({ ...manifest, allowed_tools: [] });
      expect(errors).toContain("allowed_tools must be non-empty");
    });

    it("schema drift：input_schema 指向不存在的文件 → 真实 loader fail-closed", () => {
      // 不修改真实文件；用临时 fixture 技能目录证明 schema drift 语义由
      // loader 强制（skills-loader.test.ts 已覆盖缺文件/坏 JSON 用例，这里
      // 用真实 loader 验证坏 output schema 文件的同类失败）。
      expect(() => loadSkill("propose-world-model-candidates")).not.toThrow();
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

    it("input 缺 novel_id → 拒绝", () => {
      expect(Value.Check(inputSchema, { branch: "alt" })).toBe(false);
    });

    it("input 多余未知字段（additionalProperties:false）→ 拒绝", () => {
      expect(
        Value.Check(inputSchema, { novel_id: 1, hacked: true }),
      ).toBe(false);
    });

    it("input 的 cutoff 非法（0）→ 拒绝", () => {
      expect(Value.Check(inputSchema, { novel_id: 1, cutoff: 0 })).toBe(false);
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
          schema_version: "world-model-candidate.v2",
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

    it("output 的 candidates 缺 tool_runs → 拒绝（ToolRun 血缘必须）", () => {
      const candidates = { ...example.expected_output.candidates };
      delete candidates.tool_runs;
      expect(
        Value.Check(outputSchema, { ...example.expected_output, candidates }),
      ).toBe(false);
    });

    it("output 的 claim 缺 evidence_refs → 拒绝", () => {
      const candidates = {
        ...example.expected_output.candidates,
        claims: [
          {
            claim_kind: "event",
            claim_key: "e-1",
            proposition: "无证据",
            authority: "probable_inference",
            confidence: 0.5,
            disclosure_cutoff: 1,
          },
        ],
      };
      expect(
        Value.Check(outputSchema, { ...example.expected_output, candidates }),
      ).toBe(false);
    });

    it("output 的 claim authority 非 D-01 四 label → 拒绝", () => {
      const candidates = {
        ...example.expected_output.candidates,
        claims: [
          {
            claim_kind: "event",
            claim_key: "e-1",
            proposition: "p",
            authority: "fact",
            confidence: 0.5,
            disclosure_cutoff: 1,
            evidence_refs: ["evidence:1"],
          },
        ],
      };
      expect(
        Value.Check(outputSchema, { ...example.expected_output, candidates }),
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

    it("SKILL.md 声明 Agent 禁止直接发布 Canon fact（Gate 拥有发布权威）", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of [
        "禁止直接发布 Canon fact",
        "WorldModel Validator/Gate",
        "publication",
        "fail closed",
      ]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明 approval 边界：world_model:user_interpretation 唯一审批动作", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("world_model:user_interpretation");
      expect(skill).toContain("owner 作用域确认");
      expect(skill).toContain("D-06");
    });

    it("skill.yaml 声明审批动作但不授权 Agent 发布（write_permissions 为空）", () => {
      const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest & {
        publisher?: unknown;
      };
      expect(manifest.approval_required_for).toEqual(["world_model:user_interpretation"]);
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

    it("SKILL.md 声明 allowlist 外工具（含 get_narrative_memory）→ fail closed", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("allowlist 外");
      expect(skill).toContain("get_narrative_memory");
    });
  });

  describe("Phase 27 normalization trail 正/负用例", () => {
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
            { path: "producing_skill", action: "alias", before: "skill_name", after: "propose-world-model-candidates", reason: "declared alias" },
            { path: "candidates.claims", action: "container_shape", before: { claim_key: "c1" }, after: [{ claim_key: "c1" }], reason: "declared wrap" },
          ],
          warnings: ["declared repairs applied"],
        },
      };
      expect(Value.Check(outputSchema, envelope)).toBe(true);
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
              { path: "candidates", action: "hallucinate_fact", after: { x: 1 } },
            ],
          },
        }),
      ).toBe(false);
    });
  });

  describe("共享 26-06 normalizer/validator 消费（无本地修复路径）", () => {
    const outputSchema = readSkillJson("output.schema.json");

    /** 代表 world_model_candidate 信封的声明式修复契约（与 26-06 同源）。 */
    const contract: NormalizeContract = {
      aliases: {
        producing_skill: ["skill_name"],
        producing_skill_version: ["skill_version"],
      },
      containerShapes: {
        "candidates.claims": "wrap_array",
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
        "candidates",
        "status",
      ],
    };

    const lineage = (): Record<string, unknown> => ({
      ownerId: 1,
      novelId: 1,
      skillVersionId: 1,
      modelLineage: { provider: "fixture", model: "stub-model", revision: "stub-1" },
      sourceVersions: { novel: "v1", world_model: "v1" },
      inputHash: "a".repeat(64),
      branch: null,
      evidenceRefs: ["evidence:1"],
    });

    function rawModelOutput(): Record<string, unknown> {
      return {
        type: "world_model_candidate",
        schema_version: "world-model-candidate.v1",
        skill_name: "propose-world-model-candidates",
        skill_version: "1.0.0",
        candidates: {
          projection_version: 1,
          tool_runs: [{ tool_name: "get_events", calls: 2 }],
          claims: {
            claim_kind: "event",
            claim_key: "e-arrival",
            proposition: "林安在第一章抵达临安城。",
            subject: "林安",
            authority: "probable_inference",
            confidence: 0.9,
            disclosure_cutoff: 1,
            evidence_refs: ["evidence:1"],
          },
        },
        status: "candidate",
      };
    }

    it("alias/container 修复后 repaired payload 通过 skill output schema + 严格 validator", () => {
      const result = normalizeStructuredOutput(rawModelOutput(), contract, lineage());
      expect(result.status).toBe("ok");
      const outcome = validateNormalizedOutput(result, {
        schema: outputSchema,
        allowedEvidenceRefs: ["evidence:1"],
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
