/**
 * detect-key-scenes 的场景候选集投影（Slice A：哈希字段程序产出）。
 *
 * 与 analyze-chapter 的 projectChapterLineage 同一纪律：模型只产出语义字段
 * （从运行时 get_evidence_span 结果里**选择** evidence_key + coordinates /
 * salience_reasons / score），全部哈希与血缘字段由本模块确定性注入：
 *   - set 级：schema_version / artifact_kind / owner / novel / version_key /
 *     revision_number / source_snapshot_id/hash / cutoff_chapter / schema_hash /
 *     policy_hash / detector / manifest_hash / review_state；
 *   - candidate 级：candidate_key / candidate_order / scene_id / chapter_number /
 *     source_hash / spoiler_cutoff / detector / policy_hash / review_state；
 *   - evidence 级：完整 SceneEvidenceRange（来自运行时 span + run 血缘）。
 *
 * manifest_hash 用与后端 canonical_key_scene_hash 逐字节一致的序列化重放
 * （含 Python float 语义：整数值 float 序列化为 "N.0"）；黄金值由后端
 * recompute_manifest_hash 生成并在测试中钉住。
 *
 * fail closed：模型引用未物化的 evidence_key、span 超 cutoff、空 candidates
 * 一律抛错，绝不伪造血缘。
 */

import { createHash } from "node:crypto";
import { canonicalHash } from "./normalizer.js";
import type { ToolEvidence } from "../tools/tool-evidence.js";

type JsonObject = Record<string, unknown>;

/** 投影上下文（run 权威血缘；snapshot hash / cutoff 由后端在 run input 锚定）。 */
export interface SceneProjectionContext {
  ownerId: number;
  novelId: number;
  runId: string;
  sourceSnapshotHash: string;
  cutoffChapter: number;
  /** 可选 version_key（缺省派生 ks-backfill-run-{runId}，幂等可重放）。 */
  versionKey?: string;
}

/** get_evidence_span 工具结果物化的 leaf 跨度（运行时证据）。 */
export interface RuntimeSpan {
  evidence_key: string;
  chapter_id: number;
  chapter_number: number;
  source_start: number;
  source_end: number;
  content_hash: string;
  excerpt: string | null;
}

const KEY_SCENE_SCHEMA_VERSION = "key-scene.v1";
const KEY_SCENE_ARTIFACT_KIND = "key_scene";
const KEY_SCENE_DETECTOR_ID = "key-scene.v1";
const KEY_SCENE_DETECTOR_VERSION = "1.0.0";

/** 冻结 salience reason-code 词表（镜像后端 KEY_SCENE_REASON_CODES）。 */
const KEY_SCENE_REASON_CODES = new Set([
  "plot_turn",
  "emotional_peak",
  "character_salience",
  "visual_expressiveness",
  "arc_impact",
  "quiet_emotional",
  "dialogue_turn",
  "repetition_penalty",
  "diversity_quota",
  "ambiguity_warning",
  "detector_fallback",
  "evidence_boundary",
  "no_scene_boundaries",
  "malformed_range",
  "beyond_cutoff",
]);

/** 与后端 KEY_SCENE_SCHEMA_HASH 同一 canonical 口径（黄金值钉在测试里）。 */
export const KEY_SCENE_SCHEMA_HASH = canonicalHash({
  kind: "key_scene.schema",
  schema_version: KEY_SCENE_SCHEMA_VERSION,
});

/** 与后端 DEFAULT_SCENE_POLICY.payload() 一致的评分策略负载。 */
const KEY_SCENE_POLICY_PAYLOAD: JsonObject = {
  kind: "key_scene.scoring_policy",
  version: "key-scene-scorer.v1",
  plot_turn_weight: 0.25,
  emotion_weight: 0.2,
  character_salience_weight: 0.15,
  visual_weight: 0.05,
  dialogue_weight: 0.05,
  arc_impact_weight: 0.15,
  coverage_weight: 0.15,
  evidence_base: 0.05,
  embedding_bonus_cap: 0.05,
  reason_threshold: 0.05,
  overlap_threshold: 0.55,
  diversity_bonus: 0.1,
  max_candidates: 24,
};

/** 与后端 policy_hash(DEFAULT_SCENE_POLICY) 同一 canonical 口径。 */
export const KEY_SCENE_POLICY_HASH = canonicalHash(KEY_SCENE_POLICY_PAYLOAD);

/**
 * Python json.dumps(sort_keys, separators=(",",":"), ensure_ascii=False) 兼容
 * 序列化：float 字段（score_total / salience score / score_breakdown 值）的
 * 整数值必须序列化为 "N.0"（pydantic 把这些字段强制为 float）。
 */
const FLOAT_KEYS = new Set(["score_total", "score", "score_breakdown"]);

function pythonCanonicalJson(value: unknown, forceFloat: boolean): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number") {
    if (forceFloat && Number.isInteger(value)) return `${value}.0`;
    return String(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) {
    return `[${value.map((item) => pythonCanonicalJson(item, forceFloat)).join(",")}]`;
  }
  const record = value as JsonObject;
  const parts: string[] = [];
  for (const key of Object.keys(record).sort()) {
    const childFloat = forceFloat || FLOAT_KEYS.has(key);
    parts.push(
      `${JSON.stringify(key)}:${pythonCanonicalJson(record[key], childFloat)}`,
    );
  }
  return `{${parts.join(",")}}`;
}

function sceneCanonicalHash(payload: JsonObject): string {
  return createHash("sha256")
    .update(pythonCanonicalJson(payload, false), "utf8")
    .digest("hex");
}

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function arrayOf(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function requireInt(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new Error(`scene-projection: span ${field} must be an integer`);
  }
  return value;
}

function requireHash(value: unknown, field: string): string {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`scene-projection: span ${field} must be a 64-hex hash`);
  }
  return value;
}

/** 从运行时工具证据收集 get_evidence_span 物化的 span（选择制的可选集）。 */
export function collectRuntimeSpans(
  runtimeEvidences: ToolEvidence[],
): Map<string, RuntimeSpan> {
  const spans = new Map<string, RuntimeSpan>();
  for (const evidence of runtimeEvidences) {
    if (evidence.toolName !== "get_evidence_span") continue;
    let payload: unknown;
    try {
      payload = JSON.parse(evidence.content);
    } catch {
      throw new Error("scene-projection: get_evidence_span result is not JSON");
    }
    if (!isObject(payload)) {
      throw new Error("scene-projection: get_evidence_span result is not an object");
    }
    const span: RuntimeSpan = {
      evidence_key:
        typeof payload.evidence_key === "string" ? payload.evidence_key : "",
      chapter_id: requireInt(payload.chapter_id, "chapter_id"),
      chapter_number: requireInt(payload.chapter_number, "chapter_number"),
      source_start: requireInt(payload.source_start, "source_start"),
      source_end: requireInt(payload.source_end, "source_end"),
      content_hash: requireHash(payload.content_hash, "content_hash"),
      excerpt: typeof payload.excerpt === "string" ? payload.excerpt : null,
    };
    if (!span.evidence_key) {
      throw new Error("scene-projection: span missing evidence_key");
    }
    spans.set(span.evidence_key, span);
  }
  return spans;
}

/** 与后端 candidate_canonical_payload 逐字段镜像（manifest 重放输入）。 */
function candidateCanonicalPayload(candidate: JsonObject): JsonObject {
  const coordinates = candidate.coordinates as JsonObject;
  const reasons = candidate.salience_reasons as JsonObject[];
  const ranges = candidate.evidence_ranges as JsonObject[];
  return {
    candidate_key: candidate.candidate_key,
    candidate_order: candidate.candidate_order,
    scene_id: candidate.scene_id,
    chapter_id: candidate.chapter_id,
    chapter_number: candidate.chapter_number,
    source_start: candidate.source_start,
    source_end: candidate.source_end,
    source_hash: candidate.source_hash,
    coordinates: {
      cast: coordinates.cast,
      place: coordinates.place,
      time: coordinates.time,
      pov: coordinates.pov,
    },
    spoiler_cutoff: candidate.spoiler_cutoff,
    salience_reasons: reasons.map((reason) => ({
      reason_code: reason.reason_code,
      detail: reason.detail,
      score: reason.score,
    })),
    score_total: candidate.score_total,
    score_breakdown: candidate.score_breakdown,
    diversity_key: candidate.diversity_key,
    detector_id: candidate.detector_id,
    detector_version: candidate.detector_version,
    policy_hash: candidate.policy_hash,
    evidence_keys: ranges.map((range) => range.evidence_key),
    heuristic_signal: null,
  };
}

/** 与后端 set_manifest_payload 逐字段镜像。 */
function setManifestPayload(set: JsonObject): JsonObject {
  return {
    artifact_kind: KEY_SCENE_ARTIFACT_KIND,
    schema_version: KEY_SCENE_SCHEMA_VERSION,
    owner_id: set.owner_id,
    novel_id: set.novel_id,
    version_key: set.version_key,
    revision_number: set.revision_number,
    parent_set_id: set.parent_set_id,
    source_snapshot_id: set.source_snapshot_id,
    source_snapshot_hash: set.source_snapshot_hash,
    cutoff_chapter: set.cutoff_chapter,
    schema_hash: set.schema_hash,
    policy_hash: set.policy_hash,
    detector_id: set.detector_id,
    detector_version: set.detector_version,
    approved_visual_bible_revision_id: set.approved_visual_bible_revision_id,
    approved_visual_bible_revision_hash: set.approved_visual_bible_revision_hash,
    candidates: (set.candidates as JsonObject[]).map(candidateCanonicalPayload),
  };
}

/**
 * 把模型的语义候选投影为完整 SceneCandidateSetContract。
 *
 * 模型只需为每个候选提供 ``evidence_key``（必须来自运行时 get_evidence_span
 * 结果）+ 可选语义字段（coordinates / salience_reasons / score_total /
 * score_breakdown / diversity_key）；其余字段全部程序注入，模型提供的同名
 * 字段一律忽略。
 */
export function projectSceneCandidateSet(
  content: JsonObject,
  ctx: SceneProjectionContext,
  runtimeEvidences: ToolEvidence[],
): JsonObject {
  const spans = collectRuntimeSpans(runtimeEvidences);
  const modelCandidates = arrayOf(content.candidates);
  if (modelCandidates.length === 0) {
    throw new Error(
      "scene-projection: at least one candidate is required (fail closed)",
    );
  }
  const versionKey = ctx.versionKey ?? `ks-backfill-run-${ctx.runId}`;
  const snapshotId = `ks-${versionKey}`;

  const candidates: JsonObject[] = modelCandidates.map((raw, index) => {
    if (!isObject(raw)) {
      throw new Error("scene-projection: candidate must be an object");
    }
    const evidenceKey = raw.evidence_key;
    if (typeof evidenceKey !== "string" || !evidenceKey) {
      throw new Error(
        "scene-projection: candidate requires evidence_key selected from get_evidence_span results",
      );
    }
    const span = spans.get(evidenceKey);
    if (span === undefined) {
      throw new Error(
        `scene-projection: evidence_key ${evidenceKey} was not materialized by a runtime get_evidence_span call (fail closed)`,
      );
    }
    if (span.chapter_number > ctx.cutoffChapter) {
      throw new Error(
        `scene-projection: evidence chapter ${span.chapter_number} exceeds cutoff ${ctx.cutoffChapter} (fail closed)`,
      );
    }
    // 域契约 chapter_number >= 1；chapter 0 多为前言/声明页。投影侧拦截
    // （而非后端 finalize 拒绝），poller repair loop 才能会话内修复。
    if (span.chapter_number < 1) {
      throw new Error(
        `scene-projection: evidence span chapter_number ${span.chapter_number} must be >= 1 (fail closed) — chapter 0 is front-matter; materialize a span from a main-text chapter (chapter_number >= 1) via search_novel_text + get_evidence_span and select that evidence_key instead`,
      );
    }
    const modelCoordinates = isObject(raw.coordinates) ? raw.coordinates : {};
    const coordinates = {
      cast: arrayOf(modelCoordinates.cast).filter(
        (item): item is string => typeof item === "string",
      ),
      place: typeof modelCoordinates.place === "string" ? modelCoordinates.place : null,
      time: typeof modelCoordinates.time === "string" ? modelCoordinates.time : null,
      pov: typeof modelCoordinates.pov === "string" ? modelCoordinates.pov : null,
    };
    const salienceReasons = arrayOf(raw.salience_reasons)
      .filter(isObject)
      .map((reason) => {
        const reasonCode = String(reason.reason_code ?? "");
        // 投影侧校验冻结词表（而非后端 finalize 拒绝），poller repair loop
        // 才能会话内修复。
        if (!KEY_SCENE_REASON_CODES.has(reasonCode)) {
          throw new Error(
            `scene-projection: salience reason_code ${reasonCode} is outside the frozen vocabulary (fail closed)`,
          );
        }
        const score = reason.score;
        if (
          score !== null &&
          score !== undefined &&
          (typeof score !== "number" || score < 0 || score > 1)
        ) {
          throw new Error(
            `scene-projection: salience score must be within [0,1] (fail closed)`,
          );
        }
        return {
          reason_code: reasonCode,
          detail: typeof reason.detail === "string" ? reason.detail : null,
          score: typeof score === "number" ? score : null,
        };
      });
    const sceneId = `scene-c${span.chapter_id}-${span.source_start}-${span.source_end}`;
    return {
      candidate_key: `${versionKey}-${index}`,
      candidate_order: index,
      scene_id: sceneId,
      chapter_id: span.chapter_id,
      chapter_number: span.chapter_number,
      source_start: span.source_start,
      source_end: span.source_end,
      source_hash: span.content_hash,
      coordinates,
      spoiler_cutoff: ctx.cutoffChapter,
      salience_reasons: salienceReasons,
      score_total: typeof raw.score_total === "number" ? raw.score_total : 0,
      score_breakdown: isObject(raw.score_breakdown) ? raw.score_breakdown : {},
      diversity_key:
        typeof raw.diversity_key === "string" && raw.diversity_key
          ? raw.diversity_key
          : sceneId,
      detector_id: KEY_SCENE_DETECTOR_ID,
      detector_version: KEY_SCENE_DETECTOR_VERSION,
      policy_hash: KEY_SCENE_POLICY_HASH,
      evidence_ranges: [
        {
          evidence_key: span.evidence_key,
          source_snapshot_id: snapshotId,
          source_snapshot_hash: ctx.sourceSnapshotHash,
          chapter_id: span.chapter_id,
          chapter_number: span.chapter_number,
          source_start: span.source_start,
          source_end: span.source_end,
          content_hash: span.content_hash,
          excerpt: span.excerpt === null ? null : span.excerpt.slice(0, 300),
          cutoff_chapter: ctx.cutoffChapter,
        },
      ],
      heuristic_signal: null,
      review_state: "candidate",
    } satisfies JsonObject;
  });

  const set: JsonObject = {
    schema_version: KEY_SCENE_SCHEMA_VERSION,
    artifact_kind: KEY_SCENE_ARTIFACT_KIND,
    owner_id: ctx.ownerId,
    novel_id: ctx.novelId,
    version_key: versionKey,
    revision_number: 1,
    parent_set_id: null,
    source_snapshot_id: snapshotId,
    source_snapshot_hash: ctx.sourceSnapshotHash,
    cutoff_chapter: ctx.cutoffChapter,
    schema_hash: KEY_SCENE_SCHEMA_HASH,
    policy_hash: KEY_SCENE_POLICY_HASH,
    detector_id: KEY_SCENE_DETECTOR_ID,
    detector_version: KEY_SCENE_DETECTOR_VERSION,
    manifest_hash: "0".repeat(64),
    approved_visual_bible_revision_id: null,
    approved_visual_bible_revision_hash: null,
    candidates,
    review_state: "candidate",
  };
  set.manifest_hash = sceneCanonicalHash(setManifestPayload(set));
  return set;
}
