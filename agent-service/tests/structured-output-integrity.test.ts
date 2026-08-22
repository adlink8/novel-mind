/**
 * structured-output-integrity.test.ts（26-06 / REQ-AGENT-08 / D-16）。
 *
 * 正例 + adversarial 覆盖共享 conservative normalizer 与 strict post-repair
 * validator：
 *   - 合法 alias / enum canonicalization / 无歧义 container shape → 可重放 repaired payload
 *   - 所有修复可审计（path/action/before/after/reason）；hash/action/warning 稳定
 *   - 不安全或超出 allowlist 的修复稳定 blocked；不返回可发布 payload
 *   - 受保护字段（evidence_refs/owner/cutoff/authority/branch/fork/approval）
 *     绝不由 normalizer 创建
 *   - heuristic candidate-only 无 EvidenceRef 资格 → validator blocked
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import {
  normalizeStructuredOutput,
  canonicalHash,
  canonicalJson,
  PROTECTED_FIELDS,
  type NormalizeContract,
  type NormalizeResult,
} from "../src/structured-output/normalizer.js";
import {
  validateNormalizedOutput,
  assertValidStructuredOutput,
  StructuredOutputBlockedError,
} from "../src/structured-output/validator.js";

const OUTPUT_SCHEMA = JSON.parse(
  readFileSync(
    new URL("../src/skills/answer-reading-question/output.schema.json", import.meta.url),
    "utf8",
  ),
) as Record<string, unknown>;

/** 代表 cited_answer 信封的声明式修复契约（26-05 消费同一份）。 */
const ENVELOPE_CONTRACT: NormalizeContract = {
  aliases: {
    producing_skill: ["skill_name"],
    producing_skill_version: ["skill_version"],
  },
  enumMaps: {
    "answer.uncertainty.reason_code": {
      insufficient_evidence: "missing_evidence",
      missing_reader: "unavailable",
    },
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
    "owner_id",
    "novel_id",
    "branch",
    "producing_skill",
    "producing_skill_version",
    "skill_version_id",
    "model_lineage",
    "source_versions",
    "input_hash",
    "evidence_refs",
    "answer",
    "status",
  ],
};

function rawModelOutput(withUnknownField = true): Record<string, unknown> {
  const raw: Record<string, unknown> = {
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
      uncertainty: { reason_code: "insufficient_evidence" },
    },
    status: "candidate",
  };
  if (withUnknownField) {
    raw.produced_by_model = "stub-model"; // 未知字段：normalizer 保留（不丢弃）
  }
  return raw;
}

function lineage(): Record<string, unknown> {
  return {
    ownerId: 7,
    novelId: 3,
    skillVersionId: 42,
    modelLineage: { provider: "fixture", model: "stub-model", revision: "stub-1" },
    sourceVersions: { novel: "v1", chapters: { "1": "a".repeat(64) } },
    inputHash: "b".repeat(64),
    branch: null,
    evidenceRefs: ["evidence:1"],
  };
}

// ────────────────────────── 跨语言 canonical hash 稳定性 ──────────────────────────

describe("canonical hash 与后端 python 口径一致", () => {
  it("简单嵌套对象", () => {
    expect(canonicalJson({ b: 2, a: 1 })).toBe('{"a":1,"b":2}');
    expect(canonicalHash({ b: 2, a: 1 })).toBe(
      "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777",
    );
  });

  it("中文文本（ensure_ascii=False 等价）", () => {
    expect(canonicalHash({ text: "阿宁" })).toBe(
      "076f77aa491996c8c4c7310a9694ee52db928bb37a48a872690a5090d10ca6bc",
    );
  });

  it("数组 + null + 中文", () => {
    expect(canonicalHash({ a: [{ x: 1, y: "阿宁" }], z: null })).toBe(
      "2b330aba8810faf79a883962afde3352ba5c4db94bae23d7a30eb9644b746c55",
    );
  });
});

// ────────────────────────── normalizer 正例 ──────────────────────────

describe("normalizer 允许的保守修复", () => {
  it("alias 修复：声明别名移到 canonical key，raw 保持不可变", () => {
    const raw = rawModelOutput(false);
    const snapshot = JSON.stringify(raw);
    const result = normalizeStructuredOutput(raw, ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const repaired = result.repaired as Record<string, unknown>;
    expect(repaired.producing_skill).toBe("answer-reading-question");
    expect(repaired.producing_skill_version).toBe("1.0.0");
    expect("skill_name" in repaired).toBe(false);
    expect("skill_version" in repaired).toBe(false);
    // raw immutable audit input
    expect(JSON.stringify(raw)).toBe(snapshot);
    // 审计 action 记录 path/action/before/after/reason
    const aliasActions = result.normalization_actions.filter((a) => a.action === "alias");
    expect(aliasActions).toHaveLength(2);
    expect(aliasActions[0].path).toBe("producing_skill");
    expect(aliasActions[0].before).toBe("answer-reading-question");
    expect(aliasActions[0].reason).toContain("alias");
  });

  it("alias 与 canonical 同时出现且值一致 → 去重（alias_dedup）不 block", () => {
    const raw = rawModelOutput(false);
    raw.producing_skill = "answer-reading-question"; // canonical 已存在且一致
    const result = normalizeStructuredOutput(raw, ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const repaired = result.repaired as Record<string, unknown>;
    expect(repaired.producing_skill).toBe("answer-reading-question");
    expect("skill_name" in repaired).toBe(false);
    const dedup = result.normalization_actions.find((a) => a.action === "alias_dedup");
    expect(dedup).toBeTruthy();
  });

  it("enum canonicalization：raw 值映射到 canonical", () => {
    const result = normalizeStructuredOutput(rawModelOutput(false), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const answer = (result.repaired as Record<string, unknown>).answer as Record<string, unknown>;
    const uncertainty = answer.uncertainty as Record<string, unknown>;
    expect(uncertainty.reason_code).toBe("missing_evidence");
    const enumAction = result.normalization_actions.find(
      (a) => a.action === "enum_canonicalize",
    );
    expect(enumAction).toBeTruthy();
    expect(enumAction?.before).toBe("insufficient_evidence");
    expect(enumAction?.after).toBe("missing_evidence");
  });

  it("无歧义 container shape：单对象 → 数组（wrap）", () => {
    const result = normalizeStructuredOutput(rawModelOutput(false), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const answer = (result.repaired as Record<string, unknown>).answer as Record<string, unknown>;
    expect(Array.isArray(answer.answer_blocks)).toBe(true);
    expect((answer.answer_blocks as unknown[]).length).toBe(1);
    const shapeAction = result.normalization_actions.find(
      (a) => a.action === "container_shape",
    );
    expect(shapeAction?.path).toBe("answer.answer_blocks");
  });

  it("无歧义 container shape：单元素数组 → 对象（unwrap）", () => {
    const contract: NormalizeContract = {
      containerShapes: { "answer.single": "unwrap_array" },
      requiredFields: ["answer"],
    };
    const result = normalizeStructuredOutput(
      { answer: { single: [{ x: 1 }] } },
      contract,
    );
    expect(result.status).toBe("ok");
    const answer = (result.repaired as Record<string, unknown>).answer as Record<string, unknown>;
    expect(Array.isArray(answer.single)).toBe(false);
    expect((answer.single as { x: number }).x).toBe(1);
  });

  it("lineage 合并：受保护字段只经声明 lineage 进入（服务端权威值）", () => {
    const result = normalizeStructuredOutput(rawModelOutput(false), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const repaired = result.repaired as Record<string, unknown>;
    expect(repaired.owner_id).toBe(7);
    expect(repaired.novel_id).toBe(3);
    expect(repaired.skill_version_id).toBe(42);
    expect(repaired.input_hash).toBe("b".repeat(64));
    expect(repaired.branch).toBeNull();
    expect(repaired.evidence_refs).toEqual(["evidence:1"]);
    const merges = result.normalization_actions.filter((a) => a.action === "lineage_merge");
    expect(merges.length).toBeGreaterThanOrEqual(8);
  });

  it("未知字段保留（不丢弃），由后续 validator schema 决定是否放行", () => {
    const result = normalizeStructuredOutput(rawModelOutput(true), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    expect((result.repaired as Record<string, unknown>).produced_by_model).toBe("stub-model");
  });

  it("完全 canonical 的 payload → noop：raw_hash == repaired_hash，无 action", () => {
    // 使用 canonical key 直接构建（无 alias、enum 已是 canonical、容器已合规、
    // lineage 字段已内嵌）→ 零修复。
    const lg = lineage();
    const canonical: Record<string, unknown> = {
      type: "cited_answer",
      schema_version: "cited-answer.v1",
      producing_skill: "answer-reading-question",
      producing_skill_version: "1.0.0",
      answer: {
        answer_blocks: [
          { block_id: "b1", text: "阿宁在竹林里看见了使者的身影。", evidence_refs: ["evidence:1"] },
        ],
        uncertainty: { reason_code: "missing_evidence" },
      },
      status: "candidate",
      owner_id: lg.ownerId,
      novel_id: lg.novelId,
      skill_version_id: lg.skillVersionId,
      model_lineage: lg.modelLineage,
      source_versions: lg.sourceVersions,
      input_hash: lg.inputHash,
      branch: lg.branch,
      evidence_refs: lg.evidenceRefs,
    };
    const contract: NormalizeContract = {
      aliases: { producing_skill: ["skill_name"], producing_skill_version: ["skill_version"] },
      enumMaps: {
        "answer.uncertainty.reason_code": {
          insufficient_evidence: "missing_evidence",
          missing_reader: "unavailable",
        },
      },
      containerShapes: { "answer.answer_blocks": "wrap_array" },
      lineageFields: ENVELOPE_CONTRACT.lineageFields,
      requiredFields: ENVELOPE_CONTRACT.requiredFields,
    };
    const result = normalizeStructuredOutput(canonical, contract, lineage());
    expect(result.status).toBe("ok");
    expect(result.normalization_actions).toEqual([]);
    expect(result.raw_hash).toBe(result.repaired_hash);
  });

  it("raw 不是对象 → blocked（fail closed）", () => {
    const result = normalizeStructuredOutput([1, 2, 3], ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("blocked");
    expect(result.repaired).toBeNull();
    expect(result.repaired_hash).toBeNull();
    expect(result.blocked_reason).toContain("must be a JSON object");
  });

  it("受保护字段常量包含 REQ-AGENT-08 清单", () => {
    for (const field of [
      "evidence_refs",
      "owner",
      "cutoff",
      "authority",
      "branch",
      "fork",
      "approval",
    ]) {
      expect(PROTECTED_FIELDS).toContain(field);
    }
  });
});

// ────────────────────────── normalizer adversarial：unsafe/ambiguous → blocked ──────────────────────────

describe("normalizer 不安全/歧义修复 → 稳定 blocked", () => {
  it("alias 与 canonical 同时出现且值冲突 → blocked", () => {
    const raw = rawModelOutput(false);
    raw.producing_skill = "other-skill"; // 与 skill_name 冲突
    const result = normalizeStructuredOutput(raw, ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("blocked");
    expect(result.blocked_reason).toContain("alias-conflict");
    expect(result.repaired).toBeNull();
  });

  it("enum 映射不唯一（两个 raw 映射到同一 canonical）→ blocked", () => {
    const contract: NormalizeContract = {
      enumMaps: {
        "status": { a: "published", b: "published" },
      },
    };
    const result = normalizeStructuredOutput({ status: "a" }, contract, {});
    expect(result.status).toBe("blocked");
    expect(result.blocked_reason).toContain("non-unique");
  });

  it("歧义 container（unwrap 2 元素数组）→ blocked", () => {
    const contract: NormalizeContract = {
      containerShapes: { "answer.blocks": "unwrap_array" },
    };
    const result = normalizeStructuredOutput(
      { answer: { blocks: [{ x: 1 }, { x: 2 }] } },
      contract,
      {},
    );
    expect(result.status).toBe("blocked");
    expect(result.blocked_reason).toContain("ambiguous-container");
  });

  it("缺少必需受保护字段 → blocked（不补默认值）", () => {
    // lineage 全部提供，但契约额外要求 authority（受保护、无人提供）→ 缺失即 block。
    const contract: NormalizeContract = {
      lineageFields: ENVELOPE_CONTRACT.lineageFields,
      requiredFields: ["authority"],
    };
    const result = normalizeStructuredOutput({}, contract, lineage());
    expect(result.status).toBe("blocked");
    expect(result.blocked_reason).toContain("missing-required-field");
    expect(result.blocked_reason).toContain("authority");
    expect(result.blocked_reason).toContain("protected");
  });

  it("lineage 冲突：raw 已有不同值 → blocked", () => {
    const raw = rawModelOutput(false);
    raw.owner_id = 999; // 与 lineage ownerId=7 冲突
    const result = normalizeStructuredOutput(raw, ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("blocked");
    expect(result.blocked_reason).toContain("lineage-conflict");
  });

  it("lineage 缺失（契约声明但调用方未提供）→ blocked", () => {
    const contract: NormalizeContract = {
      lineageFields: { owner_id: "ownerId" },
    };
    const result = normalizeStructuredOutput({}, contract, {});
    expect(result.status).toBe("blocked");
    expect(result.blocked_reason).toContain("lineage-missing");
  });

  it("修复契约触及受保护字段（alias canonical = evidence_refs）→ blocked", () => {
    const contract: NormalizeContract = {
      aliases: { evidence_refs: ["refs"] },
    };
    const result = normalizeStructuredOutput({ refs: ["x"] }, contract, {});
    expect(result.status).toBe("blocked");
    expect(result.blocked_reason).toContain("contract-invalid");
    expect(result.blocked_reason).toContain("protected");
  });

  it("受保护字段合成：非 lineage 路径引入受保护字段 → blocked", () => {
    // 契约没有声明 evidence_refs 的 lineage 来源，但 requiredFields 要求它，
    // 且 normalizer 只能在合成时加入 → 直接 blocked（no synthesis path）。
    const contract: NormalizeContract = {
      requiredFields: ["evidence_refs"],
    };
    const result = normalizeStructuredOutput({}, contract, {});
    expect(result.status).toBe("blocked");
    expect(result.blocked_reason).toContain("missing-required-field");
  });
});

// ────────────────────────── validator：strict post-repair ──────────────────────────

describe("validator 严格 post-repair schema/lineage 校验", () => {
  it("合法 repaired payload 通过真实 skill output schema + hash 重放", () => {
    const result = normalizeStructuredOutput(rawModelOutput(false), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const outcome = validateNormalizedOutput(result, {
      schema: OUTPUT_SCHEMA,
      allowedEvidenceRefs: ["evidence:1"],
      requiredProtectedFields: ["owner_id", "novel_id", "evidence_refs", "input_hash"],
      requireEvidenceRefs: true,
    });
    expect(outcome.status).toBe("valid");
    expect(outcome.verified_raw_hash).toBe(result.raw_hash);
    expect(outcome.verified_repaired_hash).toBe(result.repaired_hash);
  });

  it("stale repaired_hash → blocked（重放失败）", () => {
    const result = normalizeStructuredOutput(rawModelOutput(false), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    // 篡改 repaired payload → hash 漂移
    (result.repaired as Record<string, unknown>).status = "published";
    const outcome = validateNormalizedOutput(result, {});
    expect(outcome.status).toBe("blocked");
    expect(outcome.errors.join(";")).toContain("stale repaired_hash");
  });

  it("stale raw_hash → blocked", () => {
    const result = normalizeStructuredOutput(rawModelOutput(false), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    (result as { raw: unknown }).raw = { tampered: true };
    const outcome = validateNormalizedOutput(result, {});
    expect(outcome.status).toBe("blocked");
    expect(outcome.errors.join(";")).toContain("stale raw_hash");
  });

  it("schema 违规（answer block 无 evidence）→ blocked", () => {
    // 模型产出的 block 缺少 evidence_refs：wrap 后仍是结构违规（自然违规，非篡改）。
    const raw: Record<string, unknown> = {
      type: "cited_answer",
      schema_version: "cited-answer.v1",
      skill_name: "answer-reading-question",
      skill_version: "1.0.0",
      answer: {
        answer_blocks: { block_id: "b1", text: "阿宁在竹林里看见了使者的身影。" },
      },
      status: "candidate",
    };
    const result = normalizeStructuredOutput(raw, ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const outcome = validateNormalizedOutput(result, { schema: OUTPUT_SCHEMA });
    expect(outcome.status).toBe("blocked");
    expect(outcome.errors.join(";")).toContain("schema validation failed");
  });

  it("缺失必需受保护字段 → blocked（不注入默认值）", () => {
    // 契约不声明 evidence_refs 的 lineage 来源，lineage 也不提供 → repaired
    // 自然缺失 evidence_refs（heuristic candidate 形状）。
    const lineageFields: Record<string, string> = { ...ENVELOPE_CONTRACT.lineageFields! };
    delete lineageFields.evidence_refs;
    const contract: NormalizeContract = {
      lineageFields,
      requiredFields: ["type", "producing_skill", "owner_id"],
    };
    const result = normalizeStructuredOutput(
      { type: "cited_answer", producing_skill: "answer-reading-question" },
      contract,
      lineage(),
    );
    expect(result.status).toBe("ok");
    expect((result.repaired as Record<string, unknown>).evidence_refs).toBeUndefined();
    const outcome = validateNormalizedOutput(result, {
      requiredProtectedFields: ["evidence_refs"],
    });
    expect(outcome.status).toBe("blocked");
    expect(outcome.errors.join(";")).toContain("missing protected field");
    expect(outcome.errors.join(";")).toContain("evidence_refs");
  });

  it("禁止受保护字段（authority）出现 → blocked", () => {
    // 模型输出幻觉出 authority 字段：normalizer 保留未知字段，validator 拒绝。
    const raw: Record<string, unknown> = {
      type: "cited_answer",
      schema_version: "cited-answer.v1",
      skill_name: "answer-reading-question",
      skill_version: "1.0.0",
      answer: {
        answer_blocks: [
          { block_id: "b1", text: "x", evidence_refs: ["evidence:1"] },
        ],
      },
      status: "candidate",
      authority: "model-claimed-authority",
    };
    const result = normalizeStructuredOutput(raw, ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    expect((result.repaired as Record<string, unknown>).authority).toBe(
      "model-claimed-authority",
    );
    const outcome = validateNormalizedOutput(result, { forbiddenFields: ["authority"] });
    expect(outcome.status).toBe("blocked");
    expect(outcome.errors.join(";")).toContain("authority");
  });

  it("evidence_refs 在冻结 manifest allowlist 之外 → blocked", () => {
    const result = normalizeStructuredOutput(rawModelOutput(false), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const outcome = validateNormalizedOutput(result, {
      allowedEvidenceRefs: ["evidence:2"],
      requireEvidenceRefs: true,
    });
    expect(outcome.status).toBe("blocked");
    expect(outcome.errors.join(";")).toContain("outside frozen manifest allowlist");
  });

  it("heuristic candidate-only 无 evidence 资格 → blocked（不能进 cited-answer 网关）", () => {
    const result = normalizeStructuredOutput({ type: "cited_answer" }, { requiredFields: [] }, {});
    expect(result.status).toBe("ok");
    const outcome = validateNormalizedOutput(result, { requireEvidenceRefs: true });
    expect(outcome.status).toBe("blocked");
    expect(outcome.errors.join(";")).toContain("not eligible");
  });

  it("normalizer blocked 结果 → validator 直接 fail closed", () => {
    const raw = rawModelOutput(false);
    raw.producing_skill = "other-skill"; // alias-conflict
    const result = normalizeStructuredOutput(raw, ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("blocked");
    const outcome = validateNormalizedOutput(result, {});
    expect(outcome.status).toBe("blocked");
    expect(outcome.errors.join(";")).toContain("alias-conflict");
  });

  it("assertValidStructuredOutput 抛 StructuredOutputBlockedError（agent loop fail-closed）", () => {
    const result = normalizeStructuredOutput({}, ENVELOPE_CONTRACT, {});
    expect(result.status).toBe("blocked");
    expect(() => assertValidStructuredOutput(result)).toThrowError(
      StructuredOutputBlockedError,
    );
  });
});

// ────────────────────────── 稳定性与可重放性 ──────────────────────────

describe("hash/action/warning 稳定且可重放", () => {
  it("相同输入两次规范化 → 相同 raw_hash/repaired_hash/actions", () => {
    const run = (): NormalizeResult =>
      normalizeStructuredOutput(rawModelOutput(true), ENVELOPE_CONTRACT, lineage());
    const a = run();
    const b = run();
    expect(a.status).toBe("ok");
    expect(a.raw_hash).toBe(b.raw_hash);
    expect(a.repaired_hash).toBe(b.repaired_hash);
    expect(JSON.stringify(a.normalization_actions)).toBe(
      JSON.stringify(b.normalization_actions),
    );
    expect(JSON.stringify(a.repaired)).toBe(JSON.stringify(b.repaired));
  });

  it("blocked 结果 reason 稳定", () => {
    const raw = rawModelOutput(false);
    raw.producing_skill = "other-skill";
    const reason = (): string | null =>
      normalizeStructuredOutput(raw, ENVELOPE_CONTRACT, lineage()).blocked_reason;
    expect(reason()).toBe(reason());
    expect(reason()).toContain("alias-conflict");
  });

  it("repaired payload 不因 normalization 获得 authority：actions 只含声明修复", () => {
    const result = normalizeStructuredOutput(rawModelOutput(true), ENVELOPE_CONTRACT, lineage());
    expect(result.status).toBe("ok");
    const kinds = new Set(result.normalization_actions.map((a) => a.action));
    for (const kind of kinds) {
      expect(["alias", "alias_dedup", "enum_canonicalize", "container_shape", "lineage_merge"]).toContain(
        kind,
      );
    }
  });
});
