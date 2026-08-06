/**
 * 分析 skill 的结构化产物信封构造器（Phase 40 / chat_backfill）。
 *
 * 与 cited-answer-builder 同一纪律：分析 skill（detect-key-scenes /
 * propose-world-model-candidates / build-visual-bible）的模型输出是**结构化 JSON**
 * （完整 Artifact 信封或至少携带 type-specific content 负载），不是纯文本回答。
 * poller 必须按 skill 构造对应 envelope.type：
 *   - detect-key-scenes              → scene_candidate（content: scene_candidate_set）
 *   - propose-world-model-candidates → world_model_candidate（content: candidates）
 *   - build-visual-bible             → visual_bible（content: visual_bible）
 *
 * 构造规则：
 *   - 解析模型输出 JSON（支持 markdown code fence 包裹）；解析失败 → 抛错
 *     （诚实失败，绝不伪造 cited_answer）。
 *   - 只从模型输出提取 type-specific content + tool_runs/parent_revision；
 *     owner/novel/skill_version/input_hash/evidence_refs 等 lineage 字段由
 *     run 上下文权威提供（与 cited-answer-builder 相同，经声明式 lineage 合并）。
 *   - evidence_refs 从 content 内的 leaf 证据键确定性提取（scene_candidate 的
 *     evidence_ranges[].evidence_key、world_model_candidate 的
 *     claims[].evidence_refs、visual_bible 的 claims[].evidence_refs[].evidence_key）；
 *     无证据 → 抛错（fail closed，heuristic candidate 不伪造证据）。
 *   - 任何构造失败抛错——绝不发出无证据的启发式候选（fail closed）。
 */

import { canonicalHash, normalizeStructuredOutput } from "./normalizer.js";
import type { LoadedSkill } from "../skills/loader.js";
import type { FinalizeEnvelope, RunLineageContext } from "./cited-answer-builder.js";

type JsonObject = Record<string, unknown>;

/** 每个分析 skill 的信封规格：类型 / 内容键 / 证据提取。 */
interface AnalysisEnvelopeSpec {
  type: string;
  schemaVersion: string;
  contentKey: string;
  /** scene_candidate / visual_bible 信封顶层必须携带 tool_runs（schema min 1）。 */
  requiresToolRuns: boolean;
  /** 从模型输出的 type-specific content 提取 leaf evidence 键（确定性）。 */
  collectEvidenceRefs: (content: JsonObject) => string[];
}

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function arrayOf(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

/** 声明式规格（skill_name → 信封规格；单一事实源）。 */
const ANALYSIS_SPECS: Record<string, AnalysisEnvelopeSpec> = {
  "detect-key-scenes": {
    type: "scene_candidate",
    schemaVersion: "scene-candidate.v1",
    contentKey: "scene_candidate_set",
    requiresToolRuns: true,
    collectEvidenceRefs: (content) => {
      const keys: string[] = [];
      for (const candidate of arrayOf(content.candidates)) {
        if (!isObject(candidate)) continue;
        for (const range of arrayOf(candidate.evidence_ranges)) {
          if (isObject(range) && typeof range.evidence_key === "string") {
            keys.push(range.evidence_key);
          }
        }
      }
      return unique(keys);
    },
  },
  "propose-world-model-candidates": {
    type: "world_model_candidate",
    schemaVersion: "world-model-candidate.v1",
    contentKey: "candidates",
    requiresToolRuns: false,
    collectEvidenceRefs: (content) => {
      const keys: string[] = [];
      for (const claim of arrayOf(content.claims)) {
        if (!isObject(claim)) continue;
        for (const ref of arrayOf(claim.evidence_refs)) {
          if (typeof ref === "string") keys.push(ref);
        }
      }
      return unique(keys);
    },
  },
  "build-visual-bible": {
    type: "visual_bible",
    schemaVersion: "visual-bible.v1",
    contentKey: "visual_bible",
    requiresToolRuns: true,
    collectEvidenceRefs: (content) => {
      const keys: string[] = [];
      for (const claim of arrayOf(content.claims)) {
        if (!isObject(claim)) continue;
        for (const ref of arrayOf(claim.evidence_refs)) {
          if (isObject(ref) && typeof ref.evidence_key === "string") {
            keys.push(ref.evidence_key);
          }
        }
      }
      return unique(keys);
    },
  },
};

/** 该 skill 是否由本构造器支持（分析类）。 */
export function isAnalysisSkill(skillName: string): boolean {
  return skillName in ANALYSIS_SPECS;
}

/** 解析模型输出文本为 JSON（支持 markdown code fence 包裹；失败抛错）。 */
function parseModelJson(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error("analysis-envelope: model output is empty");
  }
  const fenced = trimmed.match(/^```(?:json)?\s*([\s\S]*?)```\s*$/);
  const candidate = fenced ? fenced[1] : trimmed;
  try {
    return JSON.parse(candidate);
  } catch {
    throw new Error(
      "analysis-envelope: model output is not valid JSON (refusing to fabricate)",
    );
  }
}

/** 构造分析 skill 的 finalize 信封 + 冻结 manifest。 */
export function buildAnalysisEnvelope(
  modelJson: string,
  ctx: RunLineageContext,
  skill: LoadedSkill,
  branch: string | null,
): FinalizeEnvelope {
  const spec = ANALYSIS_SPECS[skill.name];
  if (!spec) {
    throw new Error(`analysis-envelope: skill ${skill.name} has no envelope spec`);
  }

  const parsed = parseModelJson(modelJson);
  if (!isObject(parsed)) {
    throw new Error("analysis-envelope: model output must be a JSON object");
  }

  const content = parsed[spec.contentKey];
  if (!isObject(content)) {
    throw new Error(
      `analysis-envelope: model output missing ${spec.contentKey} (skill ${skill.name})`,
    );
  }

  const evidenceRefs = spec.collectEvidenceRefs(content);
  if (evidenceRefs.length === 0) {
    throw new Error(
      "analysis-envelope: no leaf evidence refs in model output (fail closed)",
    );
  }

  // 信封骨架：type-specific content + 可保留的非 lineage 字段。
  // owner/novel/input_hash 等 lineage 字段绝不信任模型——由 run 权威合并。
  const raw: JsonObject = {
    type: spec.type,
    schema_version: spec.schemaVersion,
    branch,
    [spec.contentKey]: content,
    ...(Array.isArray(parsed.tool_runs) ? { tool_runs: parsed.tool_runs } : {}),
    ...(parsed.parent_revision !== undefined
      ? { parent_revision: parsed.parent_revision }
      : {}),
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
      spec.contentKey,
      "status",
      ...(spec.requiresToolRuns ? ["tool_runs"] : []),
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
    throw new Error(
      `analysis-envelope: normalization blocked: ${result.blocked_reason ?? "unknown"}`,
    );
  }

  const envelope = result.repaired as JsonObject;
  envelope.normalization = {
    raw_hash: result.raw_hash,
    repaired_hash: result.repaired_hash,
    normalization_actions: result.normalization_actions,
    warnings: result.warnings,
  };
  const frozenManifest = { evidence_refs: evidenceRefs };

  // 防御：repaired_hash 必须与剥离 trail 后的 payload 一致（后端重放）。
  const stripped: JsonObject = {};
  for (const [key, value] of Object.entries(envelope)) {
    if (key !== "normalization") stripped[key] = value;
  }
  if (
    canonicalHash(stripped) !==
    (envelope.normalization as { repaired_hash?: string }).repaired_hash
  ) {
    throw new Error("analysis-envelope: repaired_hash replay mismatch (internal)");
  }

  return { envelope, frozenManifest };
}
