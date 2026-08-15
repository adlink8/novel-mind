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

import { createHash } from "node:crypto";
import { canonicalHash, normalizeStructuredOutput } from "./normalizer.js";
import {
  collectRuntimeSpans,
  projectSceneCandidateSet,
} from "./scene-candidate-projection.js";
import { projectVisualBibleVersion } from "./visual-bible-projection.js";
import type { LoadedSkill } from "../skills/loader.js";
import type { FinalizeEnvelope, RunLineageContext } from "./cited-answer-builder.js";
import type { RuntimeToolRunSummary } from "../tools/tool-evidence.js";
import type { ToolEvidence } from "../tools/tool-evidence.js";

type JsonObject = Record<string, unknown>;

/** 每个分析 skill 的信封规格：类型 / 内容键 / 证据提取。 */
interface AnalysisEnvelopeSpec {
  type: string;
  schemaVersion: string;
  contentKey: string;
  /** scene_candidate / visual_bible 信封顶层必须携带 tool_runs（schema min 1）。 */
  requiresToolRuns: boolean;
  /** 从模型输出的 type-specific content 提取 leaf evidence 键（确定性）。 */
  collectEvidenceRefs: (
    content: JsonObject,
    parsed: JsonObject,
    runtimeEvidences: ToolEvidence[],
  ) => string[];
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

function namespacedHash(namespace: string, value: string): string {
  return createHash("sha256").update(`${namespace}\n${value}`, "utf8").digest("hex");
}

function chapterSource(runtimeEvidences: ToolEvidence[]): string {
  const evidence = runtimeEvidences.find((item) => item.toolName === "get_chapter");
  if (!evidence) {
    throw new Error("analysis-envelope: analyze-chapter requires get_chapter evidence");
  }
  try {
    const payload = JSON.parse(evidence.content) as { content?: unknown };
    if (typeof payload.content === "string" && payload.content.length > 0) {
      return payload.content;
    }
  } catch {
    // Unit fixtures may provide plain text; it remains genuine runtime evidence.
  }
  return evidence.content;
}

function projectChapterLineage(
  content: JsonObject,
  ctx: RunLineageContext,
  runtimeEvidences: ToolEvidence[],
): void {
  if (!ctx.chapterId || !ctx.chapterNumber) {
    throw new Error("analysis-envelope: chapter run lineage is incomplete");
  }
  const source = chapterSource(runtimeEvidences);
  const hint = typeof content.next_context_hint === "string"
    ? content.next_context_hint.slice(0, 1000)
    : null;
  content.schema_version = "chapter-analysis-artifact.v1";
  content.chapter_id = ctx.chapterId;
  content.chapter_number = ctx.chapterNumber;
  content.source_snapshot_hash = namespacedHash("novelmind.chapter-source.v1", source);
  content.input_hash = ctx.inputHash;
  content.cutoff = ctx.chapterNumber;
  content.max_length = 1200;
  content.spoiler_policy_version = "spoiler-policy.v1";
  content.chapter_digest = namespacedHash("narrative-memory.chapter-digest.v1", source);
  content.chunk_digests = [
    namespacedHash("narrative-memory.chunk-digest.v1", source),
  ];
  content.previous_context_summary =
    typeof content.previous_context_summary === "string"
      ? content.previous_context_summary.slice(0, 2000)
      : null;
  content.next_context_hint = hint;
  content.next_hint_reason_code = hint
    ? null
    : typeof content.next_hint_reason_code === "string"
      ? content.next_hint_reason_code
      : "hint_unavailable";
  content.continuity_notes =
    typeof content.continuity_notes === "string"
      ? content.continuity_notes.slice(0, 1200)
      : null;
}

/** 删除模型 payload 任意层级的 tool_runs；该保留字段只接受 runtime 事实。 */
function stripModelToolRuns(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripModelToolRuns);
  if (!isObject(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => key !== "tool_runs")
      .map(([key, item]) => [key, stripModelToolRuns(item)]),
  );
}

/** 声明式规格（skill_name → 信封规格；单一事实源）。 */
const ANALYSIS_SPECS: Record<string, AnalysisEnvelopeSpec> = {
  "analyze-chapter": {
    type: "chapter_analysis",
    schemaVersion: "chapter-analysis.v1",
    contentKey: "analysis",
    requiresToolRuns: true,
    // ChapterAnalysis.analysis does not carry leaf refs. Materialize them from
    // successful Pi tool results, never from model-claimed top-level refs.
    collectEvidenceRefs: (_content, _parsed, runtimeEvidences) =>
      runtimeEvidences.map((_item, index) => `evidence:${index + 1}`),
  },
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
  const fencedMatches = [
    ...trimmed.matchAll(/```(?:json)?\s*([\s\S]*?)```/g),
  ];
  // Accept exactly one complete fenced JSON value even when the model adds a
  // short prose prefix/suffix. Multiple blocks stay ambiguous and fail closed.
  const candidate =
    fencedMatches.length === 1 ? fencedMatches[0][1].trim() : trimmed;
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
  runtimeToolRuns: RuntimeToolRunSummary[] = [],
  runtimeEvidences: ToolEvidence[] = [],
): FinalizeEnvelope {
  const spec = ANALYSIS_SPECS[skill.name];
  if (!spec) {
    throw new Error(`analysis-envelope: skill ${skill.name} has no envelope spec`);
  }
  const allowedTools = new Set(skill.allowedTools);
  for (const summary of runtimeToolRuns) {
    if (!allowedTools.has(summary.tool_name)) {
      throw new Error(
        `analysis-envelope: runtime tool ${summary.tool_name} is outside Skill allowed_tools`,
      );
    }
  }

  const parsed = parseModelJson(modelJson);
  if (!isObject(parsed)) {
    throw new Error("analysis-envelope: model output must be a JSON object");
  }

  const rawContent = parsed[spec.contentKey];
  if (!isObject(rawContent)) {
    throw new Error(
      `analysis-envelope: model output missing ${spec.contentKey} (skill ${skill.name})`,
    );
  }

  const content = stripModelToolRuns(rawContent) as JsonObject;
  // The chapter artifact schema is a runtime contract, not model-authored
  // content. Project its immutable version so harmless model omissions cannot
  // drift or block the deterministic backend integrity gate.
  if (skill.name === "analyze-chapter") {
    projectChapterLineage(content, ctx, runtimeEvidences);
  }
  // detect-key-scenes：scene_candidate_set 是运行时契约而非模型创作内容。
  // 模型只选择 evidence_key + 语义字段；全部哈希/血缘由投影确定性注入
  // （与 analyze-chapter 同一纪律，fail closed）。
  if (skill.name === "detect-key-scenes") {
    if (!ctx.sourceSnapshotHash) {
      throw new Error(
        "analysis-envelope: detect-key-scenes requires source snapshot lineage in run input",
      );
    }
    if (!ctx.cutoffChapter) {
      throw new Error(
        "analysis-envelope: detect-key-scenes requires cutoff lineage in run input",
      );
    }
    const projected = projectSceneCandidateSet(
      content,
      {
        ownerId: ctx.ownerId,
        novelId: ctx.novelId,
        runId: ctx.runId,
        sourceSnapshotHash: ctx.sourceSnapshotHash,
        cutoffChapter: ctx.cutoffChapter,
      },
      runtimeEvidences,
    );
    for (const key of Object.keys(content)) {
      delete content[key];
    }
    Object.assign(content, projected);
  }

  // build-visual-bible：visual_bible 是运行时契约而非模型创作内容。
  // 模型只产语义 entities/claims + 选择 evidence_keys；全部哈希/血缘由
  // 投影确定性注入（与 detect-key-scenes 同一纪律，fail closed）。
  if (skill.name === "build-visual-bible") {
    if (!ctx.sourceSnapshotHash) {
      throw new Error(
        "analysis-envelope: build-visual-bible requires source snapshot lineage in run input",
      );
    }
    if (!ctx.cutoffChapter) {
      throw new Error(
        "analysis-envelope: build-visual-bible requires cutoff lineage in run input",
      );
    }
    const projected = projectVisualBibleVersion(
      content,
      {
        ownerId: ctx.ownerId,
        novelId: ctx.novelId,
        runId: ctx.runId,
        sourceSnapshotHash: ctx.sourceSnapshotHash,
        cutoffChapter: ctx.cutoffChapter,
      },
      runtimeEvidences,
    );
    for (const key of Object.keys(content)) {
      delete content[key];
    }
    Object.assign(content, projected);
  }

  // propose-world-model-candidates 选择制证据门（Slice B）：claim 引用的
  // 每个 evidence_ref 都必须来自运行时 get_evidence_span 物化结果；模型编造
  // 的 key → fail closed（此前 finalize 白名单自引用，编造可静默通过）。
  if (skill.name === "propose-world-model-candidates") {
    const materialized = new Set(collectRuntimeSpans(runtimeEvidences).keys());
    for (const claim of arrayOf(content.claims)) {
      if (!isObject(claim)) continue;
      for (const ref of arrayOf(claim.evidence_refs)) {
        if (typeof ref === "string" && !materialized.has(ref)) {
          throw new Error(
            `analysis-envelope: claim evidence_ref ${ref} was not materialized by a runtime get_evidence_span call (fail closed)`,
          );
        }
      }
    }
  }

  const evidenceRefs = spec.collectEvidenceRefs(content, parsed, runtimeEvidences);
  if (evidenceRefs.length === 0) {
    throw new Error(
      "analysis-envelope: no leaf evidence refs in model output (fail closed) — " +
        "at least one evidence-backed claim/candidate is required: pick " +
        "evidence_key(s) from the materialized get_evidence_span results",
    );
  }

  // 信封骨架：type-specific content + 可保留的非 lineage 字段。
  // owner/novel/input_hash 等 lineage 字段绝不信任模型——由 run 权威合并。
  const raw: JsonObject = {
    type: spec.type,
    schema_version: spec.schemaVersion,
    branch,
    [spec.contentKey]: content,
    // tool_runs 必须来自 Pi runtime transcript；模型输出同名字段永不采信。
    tool_runs: runtimeToolRuns,
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
    source_versions:
      skill.name === "analyze-chapter"
        ? { source_snapshot_hash: content.source_snapshot_hash }
        : {},
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
  const frozenManifest = { evidence_refs: evidenceRefs, tool_runs: runtimeToolRuns };

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
