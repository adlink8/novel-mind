/**
 * build-visual-bible 的 Visual Bible 版本投影（P1a：哈希字段程序产出）。
 *
 * 与 scene-candidate-projection 同一纪律：模型只产出语义字段——
 * entities（entity_key / entity_type / description / authority）与
 * claims（entity_key 引用 / authority / description / author / rationale /
 * 从运行时 get_evidence_span 结果里**选择**的 evidence_keys）；全部哈希与
 * 血缘字段由本模块确定性注入：
 *   - version 级：schema_version / artifact_kind / owner / novel /
 *     version_key / revision_number / parent_version_id /
 *     source_snapshot_id/hash / cutoff_chapter / schema_hash / policy_hash /
 *     manifest_hash / review_state；
 *   - entity 级：stable_id（`stable-{entity_key}` 确定性派生）/
 *     disclosure_cutoff；
 *   - claim 级：claim_key / claim_hash / cutoff_chapter / 完整
 *     VisualEvidenceRef（来自运行时 span + run 血缘）。
 *
 * claim_hash 与 manifest_hash 用与后端 canonical_visual_hash 逐字节一致的
 * 序列化重放（payload 只含 int/string/bool/null，无 Python float 语义分歧）；
 * 黄金值由后端 recompute_manifest_hash / claim_content_hash 生成并在测试中
 * 钉住。
 *
 * fail closed：模型引用未物化的 evidence_key、canon_fact 无证据、
 * interpretation 缺 author/rationale、未知 entity_key、非法枚举、重复
 * entity_key、span 超 cutoff、空 entities/claims 一律抛错，绝不伪造血缘。
 */

import { canonicalHash } from "./normalizer.js";
import type { ToolEvidence } from "../tools/tool-evidence.js";
import {
  collectRuntimeSpans,
  type RuntimeSpan,
} from "./scene-candidate-projection.js";

type JsonObject = Record<string, unknown>;

/** 投影上下文（run 权威血缘；snapshot hash / cutoff 由后端在 run input 锚定）。 */
export interface VisualProjectionContext {
  ownerId: number;
  novelId: number;
  runId: string;
  sourceSnapshotHash: string;
  cutoffChapter: number;
  /** 可选 version_key（缺省派生 vb-backfill-run-{runId}，幂等可重放）。 */
  versionKey?: string;
}

const VISUAL_SCHEMA_VERSION = "visual-bible.v1";
const VISUAL_ARTIFACT_KIND = "visual_bible";

/** 冻结枚举词表（镜像后端 VisualEntityType / VisualAuthority）。 */
const VISUAL_ENTITY_TYPES = new Set([
  "character",
  "place",
  "item",
  "faction",
  "style",
]);
const VISUAL_AUTHORITIES = new Set([
  "canon_fact",
  "probable_inference",
  "literary_interpretation",
  "user_interpretation",
]);

/** 单条 claim 的 evidence ref 上限（镜像后端 VisualClaimContract）。 */
const MAX_EVIDENCE_REFS_PER_CLAIM = 16;

/** 与后端同一 canonical 口径的 schema 常量（黄金值钉在测试里）。 */
export const VISUAL_BIBLE_SCHEMA_HASH = canonicalHash({
  kind: "visual_bible.schema",
  schema_version: VISUAL_SCHEMA_VERSION,
});

/** Visual Bible review 策略负载（candidate-only + authority 纪律）。 */
const VISUAL_BIBLE_POLICY_PAYLOAD: JsonObject = {
  kind: "visual_bible.review_policy",
  version: "visual-bible-review.v1",
  candidate_only: true,
  canon_fact_requires_evidence: true,
  interpretation_requires_author_rationale: true,
  max_evidence_refs_per_claim: MAX_EVIDENCE_REFS_PER_CLAIM,
};

/** 与后端同一 canonical 口径的 policy 常量（黄金值钉在测试里）。 */
export const VISUAL_BIBLE_POLICY_HASH = canonicalHash(VISUAL_BIBLE_POLICY_PAYLOAD);

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function arrayOf(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value) {
    throw new Error(`visual-projection: ${field} must be a non-empty string`);
  }
  return value;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/** 与后端 canonical_claim_payload 逐字段镜像（claim_hash 重放输入）。 */
function claimCanonicalPayload(claim: JsonObject): JsonObject {
  return {
    claim_key: claim.claim_key,
    entity_stable_id: claim.entity_stable_id,
    authority: claim.authority,
    description: claim.description,
    author: claim.author,
    rationale: claim.rationale,
    cutoff_chapter: claim.cutoff_chapter,
    evidence_keys: (claim.evidence_refs as JsonObject[]).map(
      (ref) => ref.evidence_key,
    ),
  };
}

/** 与后端 version_manifest_payload 逐字段镜像（manifest_hash 重放输入）。 */
function versionManifestPayload(version: JsonObject): JsonObject {
  return {
    artifact_kind: VISUAL_ARTIFACT_KIND,
    schema_version: VISUAL_SCHEMA_VERSION,
    owner_id: version.owner_id,
    novel_id: version.novel_id,
    version_key: version.version_key,
    revision_number: version.revision_number,
    parent_version_id: version.parent_version_id,
    source_snapshot_id: version.source_snapshot_id,
    source_snapshot_hash: version.source_snapshot_hash,
    cutoff_chapter: version.cutoff_chapter,
    schema_hash: version.schema_hash,
    policy_hash: version.policy_hash,
    prompt_hash: version.prompt_hash,
    model_hash: version.model_hash,
    config_hash: version.config_hash,
    style_profile: version.style_profile,
    constraints: version.constraints,
    entities: (version.entities as JsonObject[]).map((entity) => ({
      stable_id: entity.stable_id,
      entity_key: entity.entity_key,
      entity_type: entity.entity_type,
      description: entity.description,
      authority: entity.authority,
      disclosure_cutoff: entity.disclosure_cutoff,
    })),
    claims: (version.claims as JsonObject[]).map((claim) => ({
      claim_key: claim.claim_key,
      entity_stable_id: claim.entity_stable_id,
      authority: claim.authority,
      description: claim.description,
      cutoff_chapter: claim.cutoff_chapter,
      evidence_keys: (claim.evidence_refs as JsonObject[]).map(
        (ref) => ref.evidence_key,
      ),
    })),
    reference_assets: (version.reference_assets as JsonObject[]).map((asset) => ({
      asset_key: asset.asset_key,
      asset_id: asset.asset_id,
      mime_type: asset.mime_type,
      bytes_hash: asset.bytes_hash,
      rights_status: asset.rights_status,
    })),
  };
}

function resolveSpan(
  evidenceKey: unknown,
  spans: Map<string, RuntimeSpan>,
  ctx: VisualProjectionContext,
): RuntimeSpan {
  if (typeof evidenceKey !== "string" || !evidenceKey) {
    throw new Error(
      "visual-projection: evidence_keys entries must be non-empty strings",
    );
  }
  const span = spans.get(evidenceKey);
  if (span === undefined) {
    throw new Error(
      `visual-projection: evidence_key ${evidenceKey} was not materialized by a runtime get_evidence_span call (fail closed)`,
    );
  }
  if (span.chapter_number > ctx.cutoffChapter) {
    throw new Error(
      `visual-projection: evidence chapter ${span.chapter_number} exceeds cutoff ${ctx.cutoffChapter} (fail closed)`,
    );
  }
  // 域契约 chapter_number >= 1；chapter 0 多为前言/声明页。投影侧拦截
  // （而非后端 finalize 拒绝），poller repair loop 才能会话内修复。
  if (span.chapter_number < 1) {
    throw new Error(
      `visual-projection: evidence span chapter_number ${span.chapter_number} must be >= 1 (fail closed) — chapter 0 is front-matter; materialize a span from a main-text chapter (chapter_number >= 1) via search_novel_text + get_evidence_span and select that evidence_key instead`,
    );
  }
  return span;
}

/**
 * 把模型的语义 entities/claims 投影为完整 VisualBibleVersionContract。
 *
 * 模型提供的同名哈希/血缘字段一律忽略（绝不采信）；任何违反选择制或
 * authority 纪律的输入 → 抛错（fail closed，poller repair loop 会话内修复）。
 */
export function projectVisualBibleVersion(
  content: JsonObject,
  ctx: VisualProjectionContext,
  runtimeEvidences: ToolEvidence[],
): JsonObject {
  const spans = collectRuntimeSpans(runtimeEvidences);
  const versionKey = ctx.versionKey ?? `vb-backfill-run-${ctx.runId}`;
  const snapshotId = `vb-${versionKey}`;

  const modelEntities = arrayOf(content.entities).filter(isObject);
  if (modelEntities.length === 0) {
    throw new Error(
      "visual-projection: at least one entity is required (fail closed)",
    );
  }
  const seenEntityKeys = new Set<string>();
  const stableToKey = new Map<string, string>();
  const entities: JsonObject[] = modelEntities.map((raw) => {
    const entityKey = requireString(raw.entity_key, "entity_key");
    if (seenEntityKeys.has(entityKey)) {
      throw new Error(
        `visual-projection: duplicate entity_key ${entityKey} (fail closed)`,
      );
    }
    seenEntityKeys.add(entityKey);
    stableToKey.set(`stable-${entityKey}`, entityKey);
    const entityType = requireString(raw.entity_type, "entity_type");
    if (!VISUAL_ENTITY_TYPES.has(entityType)) {
      throw new Error(
        `visual-projection: entity_type ${entityType} is outside the frozen vocabulary (fail closed)`,
      );
    }
    const authority = requireString(raw.authority, "entity authority");
    if (!VISUAL_AUTHORITIES.has(authority)) {
      throw new Error(
        `visual-projection: authority ${authority} is outside the frozen vocabulary (fail closed)`,
      );
    }
    return {
      stable_id: `stable-${entityKey}`,
      entity_key: entityKey,
      entity_type: entityType,
      description: requireString(raw.description, "entity description"),
      authority,
      disclosure_cutoff: ctx.cutoffChapter,
    } satisfies JsonObject;
  });

  const modelClaims = arrayOf(content.claims).filter(isObject);
  if (modelClaims.length === 0) {
    throw new Error(
      "visual-projection: at least one claim is required (fail closed)",
    );
  }
  const claims: JsonObject[] = modelClaims.map((raw, index) => {
    // 兼容旧式输出：claim 可用 entity_key 或派生 stable_id 引用实体。
    const rawRef =
      optionalString(raw.entity_key) ?? optionalString(raw.entity_stable_id);
    const entityKey =
      rawRef !== null && seenEntityKeys.has(rawRef)
        ? rawRef
        : rawRef !== null
          ? stableToKey.get(rawRef)
          : undefined;
    if (entityKey === undefined) {
      throw new Error(
        `visual-projection: claim references unknown entity_key ${rawRef} (fail closed)`,
      );
    }
    const authority = requireString(raw.authority, "claim authority");
    if (!VISUAL_AUTHORITIES.has(authority)) {
      throw new Error(
        `visual-projection: authority ${authority} is outside the frozen vocabulary (fail closed)`,
      );
    }
    // 兼容旧式输出：evidence_refs 对象数组按 evidence_key 提取（选择制不变，
    // 仍须来自运行时 get_evidence_span 物化结果）。
    const directKeys = arrayOf(raw.evidence_keys);
    const evidenceKeys =
      directKeys.length > 0
        ? directKeys
        : arrayOf(raw.evidence_refs).map((ref) =>
            isObject(ref) ? ref.evidence_key : ref,
          );
    if (evidenceKeys.length > MAX_EVIDENCE_REFS_PER_CLAIM) {
      throw new Error(
        `visual-projection: claim exceeds ${MAX_EVIDENCE_REFS_PER_CLAIM} evidence refs (fail closed)`,
      );
    }
    let author = optionalString(raw.author);
    let rationale = optionalString(raw.rationale);
    if (authority === "canon_fact") {
      // 投影侧校验（而非后端 finalize 拒绝），repair loop 才能会话内修复。
      if (evidenceKeys.length === 0) {
        throw new Error(
          "visual-projection: canon_fact claim requires at least one evidence_key (fail closed)",
        );
      }
      author = null;
      rationale = null;
    } else {
      if (author === null) {
        throw new Error(
          `visual-projection: ${authority} claim requires an author (fail closed)`,
        );
      }
      if (rationale === null) {
        throw new Error(
          `visual-projection: ${authority} claim requires a rationale (fail closed)`,
        );
      }
    }
    const evidenceRefs = evidenceKeys.map((key) => {
      const span = resolveSpan(key, spans, ctx);
      return {
        evidence_key: span.evidence_key,
        source_snapshot_id: snapshotId,
        source_snapshot_hash: ctx.sourceSnapshotHash,
        chapter_id: span.chapter_id,
        chapter_number: span.chapter_number,
        source_start: span.source_start,
        source_end: span.source_end,
        content_hash: span.content_hash,
        excerpt: span.excerpt === null ? null : span.excerpt.slice(0, 2000),
        cutoff_chapter: ctx.cutoffChapter,
      } satisfies JsonObject;
    });
    const claim: JsonObject = {
      claim_key: `${versionKey}-${index}`,
      entity_stable_id: `stable-${entityKey}`,
      authority,
      description: requireString(raw.description, "claim description"),
      author,
      rationale,
      cutoff_chapter: ctx.cutoffChapter,
      claim_hash: "0".repeat(64),
      evidence_refs: evidenceRefs,
    };
    claim.claim_hash = canonicalHash(claimCanonicalPayload(claim));
    return claim;
  });

  const version: JsonObject = {
    schema_version: VISUAL_SCHEMA_VERSION,
    artifact_kind: VISUAL_ARTIFACT_KIND,
    owner_id: ctx.ownerId,
    novel_id: ctx.novelId,
    version_key: versionKey,
    revision_number: 1,
    parent_version_id: null,
    source_snapshot_id: snapshotId,
    source_snapshot_hash: ctx.sourceSnapshotHash,
    cutoff_chapter: ctx.cutoffChapter,
    schema_hash: VISUAL_BIBLE_SCHEMA_HASH,
    policy_hash: VISUAL_BIBLE_POLICY_HASH,
    prompt_hash: null,
    model_hash: null,
    config_hash: null,
    manifest_hash: "0".repeat(64),
    style_profile: isObject(content.style_profile) ? content.style_profile : null,
    constraints: Array.isArray(content.constraints)
      ? content.constraints.filter(isObject)
      : null,
    entities,
    claims,
    reference_assets: [],
    review_state: "candidate",
  };
  version.manifest_hash = canonicalHash(versionManifestPayload(version));
  return version;
}
