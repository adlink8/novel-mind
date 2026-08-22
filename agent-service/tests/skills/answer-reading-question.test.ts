import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { Value } from "typebox/value";
import {
  normalizeStructuredOutput,
  canonicalHash,
  type NormalizeContract,
} from "../../src/structured-output/normalizer.js";
import {
  assertValidStructuredOutput,
  validateNormalizedOutput,
  StructuredOutputBlockedError,
} from "../../src/structured-output/validator.js";

/**
 * Skill-local 契约测试（25.2-07 / 26-05）：
 * 校验 answer-reading-question skill 包自身 —— schema 有效性、D-09 字段、只读白名单、
 * registry-valid fail-closed（未知工具/禁写/审批/叙事记忆）、双 fixture 通过 schema、
 * schema-mismatch 负面用例、取消语义。26-05 增补：Phase 26 锚定/source_snapshot、
 * normalization trail、共享 26-06 normalizer/validator 消费（无本地修复路径）与
 * fail-closed 语义（无 ApprovalRequest/Publisher、wrong owner/version/cutoff、
 * schema drift → 零官方写入）。不加载 loader（25.2-05 的活），不写后端。
 */

const SKILL_DIR = new URL("../../src/skills/answer-reading-question/", import.meta.url);

// 返回 any：JSON.parse 的产物是动态 JSON；`as SkillManifest` / `Value.Check(schema, ...)`
// 需要宽松类型（25.2-05 tsc 门禁修复，不改断言语义）。
function readSkillJson(relative: string): any {
  return JSON.parse(readFileSync(new URL(relative, SKILL_DIR), "utf8"));
}

function readSkillText(relative: string): string {
  return readFileSync(new URL(relative, SKILL_DIR), "utf8");
}

/** 25.2-02 注册的 7 个域工具（域工具全集）。 */
const REGISTERED_DOMAIN_TOOLS = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "get_timeline",
  "get_relationships",
  "get_clues",
  "get_narrative_memory",
] as const;

/** 首技能白名单：6 个只读工具，排除 get_narrative_memory（Open Question 4）。 */
const EXPECTED_ALLOWED_TOOLS = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "get_timeline",
  "get_relationships",
  "get_clues",
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
 * 支持本项目 skill.yaml 语法子集的极简 YAML 解析器（顶层标量 / `[]` / `>-` block scalar /
 * 缩进 list / 缩进 dict，去注释、数值与引号归一）。足够证明该文件可被机器解析；
 * 真实 loader 的 yaml/ajv 依赖在 25.2-05 引入。
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
 * Skill-local registry-valid 校验（模拟 25.2-05 loader 的 fail-closed 门禁语义，
 * 非 loader 本身）：allowed_tools ⊆ 注册工具集、排除 get_narrative_memory、
 * write_permissions / approval_required_for 为空。返回错误列表。
 */
function validateSkillContract(m: SkillManifest): string[] {
  const errors: string[] = [];
  const registered = new Set<string>(REGISTERED_DOMAIN_TOOLS);
  const unknown = m.allowed_tools.filter((t) => !registered.has(t));
  if (unknown.length > 0) {
    errors.push(`unknown tools: ${unknown.join(", ")}`);
  }
  if (m.allowed_tools.includes("get_narrative_memory")) {
    errors.push("get_narrative_memory is excluded from the read-only whitelist");
  }
  if (m.write_permissions.length > 0) {
    errors.push("read-only skill must declare empty write_permissions");
  }
  if (m.approval_required_for.length > 0) {
    errors.push("read-only skill must declare empty approval_required_for");
  }
  return errors;
}

describe("answer-reading-question skill package", () => {
  describe("JSON Schema 文件有效性", () => {
    it("input.schema.json 是合法 JSON 且为 draft-07 对象 schema", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      expect(schema.$schema).toBe("http://json-schema.org/draft-07/schema#");
      expect(schema.type).toBe("object");
      expect(Array.isArray(schema.required)).toBe(true);
      expect(schema.required).toContain("question");
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

    it("output.schema.json 声明 D-10 信封字段（evidence_refs/input_hash/schema_version/status）", () => {
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
        "normalization",
      ]) {
        expect(schema.properties).toHaveProperty(field);
      }
      expect(schema.properties.type.const).toBe("cited_answer");
      expect(schema.properties.status.enum).toEqual([
        "candidate",
        "validated",
        "approved",
        "published",
        "rejected",
      ]);
    });

    it("output.schema.json 声明 26-06 normalization trail 形状（raw_hash/repaired_hash/actions/warnings）", () => {
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
      // actions 每项至少 path/action/after；action 仅声明修复种类（无本地修复路径）。
      expect(normalization.properties.normalization_actions.items.required).toEqual([
        "path",
        "action",
        "after",
      ]);
      expect(normalization.properties.normalization_actions.items.properties.action.enum).toEqual([
        "alias",
        "alias_dedup",
        "enum_canonicalize",
        "container_shape",
        "lineage_merge",
      ]);
    });

    it("input.schema.json 声明 Phase 26 锚定与 source_snapshot（selection/chapter_range/branch/source_snapshot）", () => {
      const schema = readSkillJson("input.schema.json") as Record<string, any>;
      for (const field of ["question", "novel_id", "branch", "chapter_range", "selection", "source_snapshot"]) {
        expect(schema.properties).toHaveProperty(field);
      }
      // Reader 选区锚（D-10）：仅引用定位，不携带原始选区文本。
      expect(schema.properties.selection.required).toEqual([
        "chapter_id",
        "source_start",
        "source_end",
        "chapter_content_hash",
      ]);
      expect(schema.properties.selection.properties.kind.const).toBe("selection");
      expect(schema.properties.selection.properties.chapter_content_hash.pattern).toBe(
        "^[0-9a-f]{64}$",
      );
      // D-07 冻结 source snapshot 血缘。
      expect(schema.properties.source_snapshot.required).toEqual(["snapshot_hash"]);
      expect(schema.properties.source_snapshot.properties.snapshot_hash.pattern).toBe(
        "^[0-9a-f]{64}$",
      );
      expect(schema.additionalProperties).toBe(false);
    });
  });

  describe("skill.yaml 契约（D-09）", () => {
    const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;

    it("可被机器解析（YAML 子集）", () => {
      expect(manifest).toBeTypeOf("object");
      expect(manifest.name).toBe("answer-reading-question");
    });

    it("声明全部 10 个 D-09 必需字段", () => {
      for (const field of D09_FIELDS) {
        expect(manifest).toHaveProperty(field);
      }
    });

    it("allowed_tools 恰为 6 个只读工具且排除 get_narrative_memory", () => {
      expect(manifest.allowed_tools.sort()).toEqual([...EXPECTED_ALLOWED_TOOLS].sort());
      expect(manifest.allowed_tools).not.toContain("get_narrative_memory");
    });

    it("write_permissions 为空数组（只读）", () => {
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
      expect(manifest.budget.max_calls).toBe(20);
      expect(manifest.budget.max_input_tokens).toBe(30000);
      expect(manifest.budget.max_output_tokens).toBe(6000);
      expect(String(manifest.budget.max_cost_usd)).toBe("1.00");
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

    it("拒绝混入 get_narrative_memory", () => {
      const errors = validateSkillContract({
        ...manifest,
        allowed_tools: [...manifest.allowed_tools, "get_narrative_memory"],
      });
      expect(errors).toContain(
        "get_narrative_memory is excluded from the read-only whitelist",
      );
    });

    it("拒绝非空 write_permissions（禁写）", () => {
      const errors = validateSkillContract({
        ...manifest,
        write_permissions: ["canon"],
      });
      expect(errors).toContain(
        "read-only skill must declare empty write_permissions",
      );
    });

    it("拒绝非空 approval_required_for（审批动作）", () => {
      const errors = validateSkillContract({
        ...manifest,
        approval_required_for: ["canon:original"],
      });
      expect(errors).toContain(
        "read-only skill must declare empty approval_required_for",
      );
    });
  });

  describe("Skill-local fixtures", () => {
    const inputSchema = readSkillJson("input.schema.json");
    const outputSchema = readSkillJson("output.schema.json");

    it("examples/basic.json input 通过 input.schema 校验", () => {
      const example = readSkillJson("examples/basic.json") as {
        input: unknown;
      };
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
      expect(
        Value.Check(inputSchema, { question: "阿宁在竹林里看见了谁？" }),
      ).toBe(false);
    });

    it("input 的 question 为数字 → 拒绝", () => {
      expect(Value.Check(inputSchema, { question: 42, novel_id: 1 })).toBe(false);
    });

    it("input 多余未知字段（additionalProperties:false）→ 拒绝", () => {
      expect(
        Value.Check(inputSchema, { question: "q", novel_id: 1, hacked: true }),
      ).toBe(false);
    });

    it("output 的 status 非法 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, { ...example.expected_output, status: "bogus" }),
      ).toBe(false);
    });

    it("output 的 evidence_refs 为空数组 → 拒绝", () => {
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

    it("output 的 answer_blocks 缺 evidence_refs → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          answer: {
            answer_blocks: [{ block_id: "b1", text: "无引证" }],
          },
        }),
      ).toBe(false);
    });
  });

  describe("取消（cancel-without-write）语义", () => {
    it("SKILL.md 声明取消 → cancelled 且零 artifact/revision 写入", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("cancel_requested");
      expect(skill).toContain("cancelled");
      expect(skill).toContain("0 artifact 行");
      expect(skill).toContain("0 revision 行");
    });

    it("skill.yaml 只读契约 → 无任何持久化通道（无写权限/无审批），取消即零写入", () => {
      const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest;
      expect(manifest.write_permissions).toEqual([]);
      expect(manifest.approval_required_for).toEqual([]);
      expect(validateSkillContract(manifest)).toEqual([]);
    });
  });

  describe("Phase 26 input 锚定与 source_snapshot 正/负用例", () => {
    const inputSchema = readSkillJson("input.schema.json");

    it("Reader selection 锚合法 → 通过", () => {
      const input = {
        question: "阿宁在竹林里看见了谁？",
        novel_id: 1,
        selection: {
          kind: "selection",
          chapter_id: 1,
          source_start: 0,
          source_end: 12,
          chapter_content_hash: "a".repeat(64),
        },
      };
      expect(Value.Check(inputSchema, input)).toBe(true);
    });

    it("Analysis chapter_range 锚 + source_snapshot 合法 → 通过", () => {
      const input = {
        question: "第一章发生了什么？",
        novel_id: 1,
        chapter_range: { chapter_start: 1, chapter_end: 5 },
        source_snapshot: {
          snapshot_hash: "b".repeat(64),
          dataset_lineage: "queryplan-questions-v1",
        },
      };
      expect(Value.Check(inputSchema, input)).toBe(true);
    });

    it("branch 非空字符串 → 通过（owner/novel/branch 血缘绑定）", () => {
      expect(
        Value.Check(inputSchema, { question: "q", novel_id: 1, branch: "alt-ending" }),
      ).toBe(true);
    });

    it("selection 的 chapter_content_hash 非 64-hex → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          question: "q",
          novel_id: 1,
          selection: {
            kind: "selection",
            chapter_id: 1,
            source_start: 0,
            source_end: 12,
            chapter_content_hash: "not-a-hash",
          },
        }),
      ).toBe(false);
    });

    it("source_snapshot 缺 snapshot_hash → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          question: "q",
          novel_id: 1,
          source_snapshot: { dataset_lineage: "queryplan-questions-v1" },
        }),
      ).toBe(false);
    });

    it("selection 缺 chapter_id → 拒绝", () => {
      expect(
        Value.Check(inputSchema, {
          question: "q",
          novel_id: 1,
          selection: { source_start: 0, source_end: 5, chapter_content_hash: "c".repeat(64) },
        }),
      ).toBe(false);
    });
  });

  describe("Phase 26 normalization trail 正/负用例", () => {
    const outputSchema = readSkillJson("output.schema.json");
    const example = readSkillJson("examples/basic.json") as { expected_output: any };
    const trail = example.expected_output.normalization as Record<string, any>;

    it("合法 normalization trail（noop 修复）→ 通过", () => {
      expect(Value.Check(outputSchema, example.expected_output)).toBe(true);
    });

    it("带 alias/enum/container 修复动作的 trail（path/action/after）→ 通过", () => {
      const envelope = {
        ...example.expected_output,
        normalization: {
          raw_hash: "0".repeat(64),
          repaired_hash: "0".repeat(64),
          normalization_actions: [
            { path: "producing_skill", action: "alias", before: "skill_name", after: "answer-reading-question", reason: "declared alias" },
            { path: "answer.answer_blocks", action: "container_shape", before: { block_id: "b1" }, after: [{ block_id: "b1" }], reason: "declared wrap" },
            { path: "status", action: "enum_canonicalize", before: "candidate", after: "candidate", reason: "declared enum" },
          ],
          warnings: ["enum canonicalization applied"],
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

    it("normalization 的 repaired_hash 非 64-hex → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          normalization: { ...trail, repaired_hash: "short" },
        }),
      ).toBe(false);
    });

    it("normalization_actions 项缺 action/after → 拒绝（修复必须可审计）", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          normalization: {
            ...trail,
            normalization_actions: [{ path: "producing_skill" }],
          },
        }),
      ).toBe(false);
    });

    it("normalization_actions 项 action 非声明修复种类 → 拒绝", () => {
      expect(
        Value.Check(outputSchema, {
          ...example.expected_output,
          normalization: {
            ...trail,
            normalization_actions: [
              { path: "answer", action: "hallucinate_fact", after: { x: 1 } },
            ],
          },
        }),
      ).toBe(false);
    });

    it("skill schema 声明但不由它强制 normalization（权威在后端 wire 模型）", () => {
      // 26-06 共享 validator 校验的是不含 trail 的 repaired payload；official 信封的
      // normalization required 强制在 backend CitedAnswerArtifact（finalize 完整性门）。
      const { normalization: _drop, ...withoutTrail } = example.expected_output;
      expect(Value.Check(outputSchema, withoutTrail)).toBe(true);
    });
  });

  describe("共享 26-06 normalizer/validator 消费（无本地修复路径）", () => {
    const outputSchema = readSkillJson("output.schema.json");

    /** 代表 cited_answer 信封的声明式修复契约（与 26-06 ENVELOPE_CONTRACT 同源）。 */
    const contract: NormalizeContract = {
      aliases: {
        producing_skill: ["skill_name"],
        producing_skill_version: ["skill_version"],
      },
      containerShapes: {
        "answer.answer_blocks": "wrap_array",
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
        "answer",
        "status",
      ],
    };

    const lineage = (): Record<string, unknown> => ({
      ownerId: 1,
      novelId: 1,
      skillVersionId: 1,
      modelLineage: { provider: "fixture", model: "stub-model", revision: "stub-1" },
      sourceVersions: { novel: "v1" },
      inputHash: "a".repeat(64),
      branch: null,
      evidenceRefs: ["evidence:1"],
    });

    function rawModelOutput(): Record<string, unknown> {
      return {
        type: "cited_answer",
        schema_version: "cited-answer.v1",
        skill_name: "answer-reading-question",
        skill_version: "1.0.0",
        answer: {
          answer_blocks: {
            block_id: "b1",
            text: "阿宁在竹林里看见了使者的身影。",
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

  describe("Phase 26 fail-closed 语义（SKILL.md / skill.yaml）", () => {
    it("SKILL.md 声明无 ApprovalRequest / 无 Publisher / 无 promotion", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("ApprovalRequest");
      expect(skill).toContain("Publisher");
      expect(skill).toContain("approval_required_for: []");
      expect(skill).toContain("fail closed");
    });

    it("SKILL.md 声明未知工具/白名单外调用 → fail closed", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("白名单外");
      expect(skill).toContain("get_narrative_memory");
    });

    it("SKILL.md 声明 wrong owner/version/cutoff/schema drift → 稳定 blocked/cancelled 零写入", () => {
      const skill = readSkillText("SKILL.md");
      for (const token of ["wrong owner", "wrong skill_version", "wrong cutoff", "schema drift", "零写入"]) {
        expect(skill).toContain(token);
      }
    });

    it("SKILL.md 声明共享 normalizer/validator，不引入本地修复路径", () => {
      const skill = readSkillText("SKILL.md").replace(/\s+/g, " ");
      expect(skill).toContain("共享 26-06 normalizer");
      expect(skill).toContain("严格 post-repair validator");
      // 粗体包夹可能跨行断词，用去空白匹配。
      expect(skill.replace(/\s+/g, "")).toContain("不引入任何本地修复路径");
      expect(skill).toContain("normalization_actions");
      expect(skill).toContain("raw_hash");
      expect(skill).toContain("repaired_hash");
      expect(skill).toContain("warnings");
    });

    it("SKILL.md 声明 heuristic candidate-only（无 evidence_refs → BLOCKED_NO_EVIDENCE）", () => {
      const skill = readSkillText("SKILL.md");
      expect(skill).toContain("BLOCKED_NO_EVIDENCE");
      expect(skill).toContain("candidate-only");
    });

    it("skill.yaml 无任何 ApprovalRequest/Publisher 动作声明", () => {
      const manifest = parseSkillYaml(readSkillText("skill.yaml")) as unknown as SkillManifest & {
        publisher?: unknown;
      };
      // 只读契约：无审批动作、无写权限、无任何 publisher/发布动作键。
      expect(manifest.approval_required_for).toEqual([]);
      expect(manifest.write_permissions).toEqual([]);
      expect("publisher" in manifest).toBe(false);
      expect(Object.keys(manifest)).not.toContain("publish_action");
    });
  });
});
