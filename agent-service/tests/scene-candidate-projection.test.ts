/**
 * scene-candidate-projection 单元测试（Slice A：哈希字段程序产出）。
 *
 * 契约：
 * - 模型只产出语义字段（选中的 evidence_key + coordinates/salience/score）；
 *   全部哈希与血缘字段（source_hash / source_snapshot_hash / schema_hash /
 *   policy_hash / manifest_hash / candidate_key / spoiler_cutoff 等）由投影
 *   确定性注入——镜像 analyze-chapter 的 projectChapterLineage 纪律。
 * - evidence_key 必须来自运行时 get_evidence_span 工具结果（选择制）；模型
 *   编造未物化的 key → fail closed。
 * - schema_hash / policy_hash / manifest_hash 必须与后端 Python
 *   canonical_key_scene_hash 逐字节一致（黄金值由后端生成，见下）。
 */

import { describe, it, expect } from "vitest";
import {
  KEY_SCENE_SCHEMA_HASH,
  KEY_SCENE_POLICY_HASH,
  projectSceneCandidateSet,
  type SceneProjectionContext,
} from "../src/structured-output/scene-candidate-projection.js";
import type { ToolEvidence } from "../src/tools/tool-evidence.js";

// 后端黄金值（backend/app/services/key_scenes/candidates.py ::
// KEY_SCENE_SCHEMA_HASH 与 scoring.py :: policy_hash(DEFAULT_SCENE_POLICY)）。
const PYTHON_SCHEMA_HASH =
  "43f633fe5ec7e915d2b5fac0f123792f9988b76c2f11960171b0a2658c30cf5e";
const PYTHON_POLICY_HASH =
  "18319fdd57b57e2fb50b53bd225029eb9eb0f9a8cf480b82b81e68870a484839";

const SNAPSHOT_HASH = "b".repeat(64);
const SPAN_CONTENT_HASH = "1".repeat(64);
const SPAN_EVIDENCE_KEY = `qp:7:0:10:${SPAN_CONTENT_HASH}`;

const CTX: SceneProjectionContext = {
  ownerId: 2,
  novelId: 6,
  runId: "1",
  sourceSnapshotHash: SNAPSHOT_HASH,
  cutoffChapter: 3,
};

const SPAN_EVIDENCE: ToolEvidence = {
  toolName: "get_evidence_span",
  content: JSON.stringify({
    evidence_key: SPAN_EVIDENCE_KEY,
    chapter_id: 7,
    chapter_number: 2,
    novel_id: 6,
    source_start: 0,
    source_end: 10,
    content_hash: SPAN_CONTENT_HASH,
    excerpt: "夜色笼罩着庭院",
  }),
};

function minimalModelContent() {
  return {
    candidates: [
      {
        evidence_key: SPAN_EVIDENCE_KEY,
        coordinates: { cast: ["林安"], place: "庭院", time: null, pov: null },
        salience_reasons: [
          { reason_code: "plot_turn", detail: "袭击发生", score: 0.9 },
        ],
        // 整数值 float：Python 侧 score_total 是 float（序列化为 1.0），
        // 黄金值必须覆盖这个跨语言序列化分歧。
        score_total: 1,
        score_breakdown: { action: 0.8 },
      },
    ],
  };
}

describe("跨语言哈希常量（Python 黄金值）", () => {
  it("KEY_SCENE_SCHEMA_HASH 与后端一致", () => {
    expect(KEY_SCENE_SCHEMA_HASH).toBe(PYTHON_SCHEMA_HASH);
  });

  it("KEY_SCENE_POLICY_HASH 与后端 DEFAULT_SCENE_POLICY 一致", () => {
    expect(KEY_SCENE_POLICY_HASH).toBe(PYTHON_POLICY_HASH);
  });
});

describe("projectSceneCandidateSet", () => {
  it("模型零哈希输出 → 投影出完整 SceneCandidateSetContract", () => {
    const set = projectSceneCandidateSet(minimalModelContent(), CTX, [
      SPAN_EVIDENCE,
    ]) as Record<string, any>;

    // set 级血缘
    expect(set.schema_version).toBe("key-scene.v1");
    expect(set.artifact_kind).toBe("key_scene");
    expect(set.owner_id).toBe(2);
    expect(set.novel_id).toBe(6);
    expect(set.version_key).toBe("ks-backfill-run-1");
    expect(set.revision_number).toBe(1);
    expect(set.parent_set_id).toBeNull();
    expect(set.source_snapshot_id).toBe("ks-ks-backfill-run-1");
    expect(set.source_snapshot_hash).toBe(SNAPSHOT_HASH);
    expect(set.cutoff_chapter).toBe(3);
    expect(set.schema_hash).toBe(PYTHON_SCHEMA_HASH);
    expect(set.policy_hash).toBe(PYTHON_POLICY_HASH);
    expect(set.detector_id).toBe("key-scene.v1");
    expect(set.detector_version).toBe("1.0.0");
    expect(set.manifest_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(set.review_state).toBe("candidate");
    expect(set.approved_visual_bible_revision_id).toBeNull();
    expect(set.approved_visual_bible_revision_hash).toBeNull();

    // candidate 级投影
    const candidate = set.candidates[0];
    expect(candidate.candidate_key).toBe("ks-backfill-run-1-0");
    expect(candidate.candidate_order).toBe(0);
    expect(candidate.scene_id).toBe("scene-c7-0-10");
    expect(candidate.chapter_id).toBe(7);
    expect(candidate.chapter_number).toBe(2);
    expect(candidate.source_start).toBe(0);
    expect(candidate.source_end).toBe(10);
    expect(candidate.source_hash).toBe(SPAN_CONTENT_HASH);
    expect(candidate.spoiler_cutoff).toBe(3);
    expect(candidate.policy_hash).toBe(PYTHON_POLICY_HASH);
    expect(candidate.detector_id).toBe("key-scene.v1");
    expect(candidate.review_state).toBe("candidate");
    expect(candidate.heuristic_signal).toBeNull();

    // evidence range 由运行时 span + run 血缘投影
    const range = candidate.evidence_ranges[0];
    expect(range.evidence_key).toBe(SPAN_EVIDENCE_KEY);
    expect(range.source_snapshot_id).toBe("ks-ks-backfill-run-1");
    expect(range.source_snapshot_hash).toBe(SNAPSHOT_HASH);
    expect(range.content_hash).toBe(SPAN_CONTENT_HASH);
    expect(range.chapter_number).toBe(2);
    expect(range.cutoff_chapter).toBe(3);
  });

  it("manifest_hash 与后端 recompute_manifest_hash 黄金值一致（跨语言重放）", () => {
    const set = projectSceneCandidateSet(minimalModelContent(), CTX, [
      SPAN_EVIDENCE,
    ]) as Record<string, any>;
    // 黄金值由后端 Python recompute_manifest_hash 对同一逻辑 payload 生成。
    expect(set.manifest_hash).toBe(
      "f483c67c16cacbe86fc349ea0cf17b90b2e2e14da15603c49a4b888462512d5c",
    );
  });

  it("模型引用未物化的 evidence_key → fail closed", () => {
    const content = {
      candidates: [{ evidence_key: "qp:7:0:10:" + "9".repeat(64) }],
    };
    expect(() =>
      projectSceneCandidateSet(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/evidence/);
  });

  it("候选区间超出 cutoff（span chapter_number > cutoff）→ fail closed", () => {
    const beyondCtx: SceneProjectionContext = { ...CTX, cutoffChapter: 1 };
    expect(() =>
      projectSceneCandidateSet(minimalModelContent(), beyondCtx, [SPAN_EVIDENCE]),
    ).toThrow(/cutoff/);
  });

  it("span 来自 chapter_number=0 的章节（前言/声明章）→ fail closed", () => {
    // 域契约 chapter_number >= 1；chapter 0 多为爬虫声明页，且投影侧拦截
    // 才能触发 poller 的 repair loop（后端 finalize 拒绝无法会话内修复）。
    const zeroSpan: ToolEvidence = {
      toolName: "get_evidence_span",
      content: JSON.stringify({
        evidence_key: "qp:5:0:10:" + "2".repeat(64),
        chapter_id: 5,
        chapter_number: 0,
        novel_id: 6,
        source_start: 0,
        source_end: 10,
        content_hash: "2".repeat(64),
        excerpt: "声明：本书为……",
      }),
    };
    const content = {
      candidates: [{ evidence_key: "qp:5:0:10:" + "2".repeat(64) }],
    };
    expect(() =>
      projectSceneCandidateSet(content, CTX, [zeroSpan]),
    ).toThrow(/chapter_number/);
  });

  it("salience reason_code 不在冻结枚举内 → fail closed（投影侧拦截，可修复）", () => {
    const content = minimalModelContent();
    (content.candidates[0] as any).salience_reasons = [
      { reason_code: "情感高潮", detail: "非枚举值", score: 0.9 },
    ];
    expect(() =>
      projectSceneCandidateSet(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/reason_code/);
  });

  it("salience score 超出 [0,1] → fail closed", () => {
    const content = minimalModelContent();
    (content.candidates[0] as any).salience_reasons = [
      { reason_code: "plot_turn", detail: null, score: 1.5 },
    ];
    expect(() =>
      projectSceneCandidateSet(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/score/);
  });

  it("空 candidates → fail closed（域契约要求至少一个候选）", () => {
    expect(() =>
      projectSceneCandidateSet({ candidates: [] }, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/candidate/);
  });

  it("模型提供的哈希/血缘字段被忽略（投影覆盖，绝不采信）", () => {
    const content = minimalModelContent();
    (content.candidates[0] as Record<string, unknown>).source_hash =
      "f".repeat(64);
    const set = projectSceneCandidateSet(content, CTX, [
      SPAN_EVIDENCE,
    ]) as Record<string, any>;
    expect(set.candidates[0].source_hash).toBe(SPAN_CONTENT_HASH);
  });
});
