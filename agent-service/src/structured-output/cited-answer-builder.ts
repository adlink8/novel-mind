/**
 * cited_answer 信封构造器（26-06 / answer-reading-question）。
 *
 * 把模型纯文本回答 + run 血缘上下文 + 工具调用证据，构造成后端 finalize
 * 完整性门（structured_output_integrity）接受的 CitedAnswerArtifact 信封：
 *   - evidence_refs 从成功只读工具调用确定性生成（evidence:1, evidence:2 …）；
 *   - lineage（owner/novel/skill_version_id/input_hash）来自 run 视图；
 *   - normalization trail 复用共享 normalizer（canonicalHash 与后端一致）。
 *
 * 任何构造失败抛错——绝不发出无证据的启发式候选（fail closed）。
 */

import { normalizeStructuredOutput, canonicalHash } from "./normalizer.js";
import type { LoadedSkill } from "../skills/loader.js";

/** 最终 finalize 请求需要的信封 + 冻结 manifest。 */
export interface FinalizeEnvelope {
  envelope: Record<string, unknown>;
  frozenManifest: Record<string, unknown>;
}

/** run 血缘上下文（来自 skill-runs 202 响应的 SkillRunView）。 */
export interface RunLineageContext {
  runId: string;
  ownerId: number;
  novelId: number;
  skillVersionId: number;
  inputHash: string;
}

/** 一次成功的只读工具调用结果（用于物化 evidence_refs）。 */
export interface ToolEvidence {
  toolName: string;
  content: string;
}

/** 构造 cited_answer 信封 + 冻结 manifest。 */
export function buildCitedAnswerEnvelope(
  modelText: string,
  ctx: RunLineageContext,
  skill: LoadedSkill,
  evidences: ToolEvidence[],
): FinalizeEnvelope {
  if (!modelText || !modelText.trim()) {
    throw new Error("cited-answer: empty model output cannot be finalized");
  }
  if (evidences.length === 0) {
    throw new Error(
      "cited-answer: no successful read-only tool calls -> no evidence_refs (fail closed)"
    );
  }

  const evidenceRefs = evidences.map((_, i) => `evidence:${i + 1}`);
  const block = {
    block_id: "b1",
    text: modelText.slice(0, 4000),
    evidence_refs: evidenceRefs,
  };

  const raw = {
    type: "cited_answer",
    schema_version: "cited-answer.v1",
    branch: null,
    answer: { answer_blocks: [block] },
    status: "candidate",
  };

  const contract = {
    requiredFields: [
      "type",
      "schema_version",
      "owner_id",
      "novel_id",
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
    lineageFields: {
      owner_id: "owner_id",
      novel_id: "novel_id",
      producing_skill: "producing_skill",
      producing_skill_version: "producing_skill_version",
      skill_version_id: "skill_version_id",
      model_lineage: "model_lineage",
      source_versions: "source_versions",
      input_hash: "input_hash",
      evidence_refs: "evidence_refs",
    },
  };

  const lineage = {
    owner_id: ctx.ownerId,
    novel_id: ctx.novelId,
    producing_skill: skill.name,
    producing_skill_version: skill.version ?? "1.0.0",
    skill_version_id: ctx.skillVersionId,
    model_lineage: { provider: "novelmind-gateway", model: "reader-chat-default" },
    source_versions: {},
    input_hash: ctx.inputHash,
    evidence_refs: evidenceRefs,
  };

  const result = normalizeStructuredOutput(raw, contract, lineage);
  if (result.status !== "ok" || result.repaired === null) {
    throw new Error(`cited-answer: normalization blocked: ${result.blocked_reason ?? "unknown"}`);
  }

  const envelope = result.repaired as Record<string, unknown>;
  // 26-06 trail：repaired_hash 是对**不含 trail** 的 payload 计算的（normalizer 已算）；
  // raw_hash/actions/warnings 由 normalizer 提供。组装进信封。
  envelope.normalization = {
    raw_hash: result.raw_hash,
    repaired_hash: result.repaired_hash,
    normalization_actions: result.normalization_actions,
    warnings: result.warnings,
  };
  const frozenManifest = { evidence_refs: evidenceRefs };

  // 防御：repaired_hash 必须与剥离 trail 后的 payload 一致（后端重放）。
  const stripped: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(envelope)) {
    if (k !== "normalization") stripped[k] = v;
  }
  if (
    canonicalHash(stripped) !==
    (envelope.normalization as { repaired_hash?: string }).repaired_hash
  ) {
    throw new Error("cited-answer: repaired_hash replay mismatch (internal)");
  }

  return { envelope, frozenManifest };
}
