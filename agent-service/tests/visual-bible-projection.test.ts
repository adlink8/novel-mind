/**
 * visual-bible-projection 单元测试（P1a：哈希字段程序产出，镜像 Slice A 纪律）。
 *
 * 契约：
 * - 模型只产出语义字段：entities（entity_key/entity_type/description/authority）
 *   与 claims（entity_key 引用 / authority / description / author / rationale /
 *   选中的 evidence_keys）；全部哈希与血缘字段（stable_id / claim_key /
 *   claim_hash / schema_hash / policy_hash / manifest_hash / source_snapshot_*
 *   / cutoff_chapter / disclosure_cutoff / review_state）由投影确定性注入。
 * - evidence_keys 必须来自运行时 get_evidence_span 工具结果（选择制）；模型
 *   编造未物化的 key → fail closed。
 * - canon_fact claim 必须至少选 1 条 evidence_key；interpretation 类 claim 必须
 *   带 author + rationale——投影侧拦截（而非后端 finalize 拒绝），poller
 *   repair loop 才能会话内修复。
 * - schema_hash / policy_hash / claim_hash / manifest_hash 必须与后端 Python
 *   canonical_visual_hash 逐字节一致（黄金值由后端生成，见下）。
 */

import { describe, it, expect } from "vitest";
import {
  VISUAL_BIBLE_SCHEMA_HASH,
  VISUAL_BIBLE_POLICY_HASH,
  projectVisualBibleVersion,
  type VisualProjectionContext,
} from "../src/structured-output/visual-bible-projection.js";
import type { ToolEvidence } from "../src/tools/tool-evidence.js";

// 后端黄金值（backend/app/schemas/visual_bible.py :: canonical_visual_hash）。
const PYTHON_SCHEMA_HASH =
  "28629924b9bd724066f0325f1ecd4a49a61975fd760cc001ce14bd20e195508b";
const PYTHON_POLICY_HASH =
  "2bde107cf4e51fe4bc0d3ffb3251eee4de5f980306382ef07387a4ca739ce111";

const SNAPSHOT_HASH = "b".repeat(64);
const SPAN_CONTENT_HASH = "1".repeat(64);
const SPAN_EVIDENCE_KEY = `qp:7:0:40:${SPAN_CONTENT_HASH}`;

const CTX: VisualProjectionContext = {
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
    source_end: 40,
    content_hash: SPAN_CONTENT_HASH,
    excerpt: "慕师靖披着白狐裘，立在城门口。",
  }),
};

function minimalModelContent() {
  return {
    style_profile: null,
    constraints: null,
    entities: [
      {
        entity_key: "char-mu-shijing",
        entity_type: "character",
        description: "慕师靖：白狐裘、立如松。",
        authority: "canon_fact",
      },
    ],
    claims: [
      {
        entity_key: "char-mu-shijing",
        authority: "canon_fact",
        description: "慕师靖入城时披白狐裘。",
        evidence_keys: [SPAN_EVIDENCE_KEY],
      },
      {
        entity_key: "char-mu-shijing",
        authority: "literary_interpretation",
        description: "白狐裘象征她与尘世的距离。",
        author: "reader-agent",
        rationale: "文本意象解读。",
      },
    ],
  };
}

describe("跨语言哈希常量（Python 黄金值）", () => {
  it("VISUAL_BIBLE_SCHEMA_HASH 与后端一致", () => {
    expect(VISUAL_BIBLE_SCHEMA_HASH).toBe(PYTHON_SCHEMA_HASH);
  });

  it("VISUAL_BIBLE_POLICY_HASH 与后端一致", () => {
    expect(VISUAL_BIBLE_POLICY_HASH).toBe(PYTHON_POLICY_HASH);
  });
});

describe("projectVisualBibleVersion", () => {
  it("模型零哈希输出 → 投影出完整 VisualBibleVersionContract", () => {
    const version = projectVisualBibleVersion(minimalModelContent(), CTX, [
      SPAN_EVIDENCE,
    ]) as Record<string, any>;

    // version 级血缘
    expect(version.schema_version).toBe("visual-bible.v1");
    expect(version.artifact_kind).toBe("visual_bible");
    expect(version.owner_id).toBe(2);
    expect(version.novel_id).toBe(6);
    expect(version.version_key).toBe("vb-backfill-run-1");
    expect(version.revision_number).toBe(1);
    expect(version.parent_version_id).toBeNull();
    expect(version.source_snapshot_id).toBe("vb-vb-backfill-run-1");
    expect(version.source_snapshot_hash).toBe(SNAPSHOT_HASH);
    expect(version.cutoff_chapter).toBe(3);
    expect(version.schema_hash).toBe(PYTHON_SCHEMA_HASH);
    expect(version.policy_hash).toBe(PYTHON_POLICY_HASH);
    expect(version.manifest_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(version.review_state).toBe("candidate");
    expect(version.reference_assets).toEqual([]);

    // entity 级投影：stable_id / disclosure_cutoff 程序派生
    const entity = version.entities[0];
    expect(entity.stable_id).toBe("stable-char-mu-shijing");
    expect(entity.entity_key).toBe("char-mu-shijing");
    expect(entity.entity_type).toBe("character");
    expect(entity.authority).toBe("canon_fact");
    expect(entity.disclosure_cutoff).toBe(3);

    // canon_fact claim：claim_key / claim_hash / evidence_refs 程序注入
    const canonClaim = version.claims[0];
    expect(canonClaim.claim_key).toBe("vb-backfill-run-1-0");
    expect(canonClaim.entity_stable_id).toBe("stable-char-mu-shijing");
    expect(canonClaim.claim_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(canonClaim.cutoff_chapter).toBe(3);
    expect(canonClaim.author).toBeNull();
    expect(canonClaim.rationale).toBeNull();
    expect(canonClaim.evidence_refs).toHaveLength(1);
    const ref = canonClaim.evidence_refs[0];
    expect(ref.evidence_key).toBe(SPAN_EVIDENCE_KEY);
    expect(ref.source_snapshot_id).toBe("vb-vb-backfill-run-1");
    expect(ref.source_snapshot_hash).toBe(SNAPSHOT_HASH);
    expect(ref.chapter_id).toBe(7);
    expect(ref.chapter_number).toBe(2);
    expect(ref.source_start).toBe(0);
    expect(ref.source_end).toBe(40);
    expect(ref.content_hash).toBe(SPAN_CONTENT_HASH);
    expect(ref.cutoff_chapter).toBe(3);

    // interpretation claim：author/rationale 保留，无证据
    const interpClaim = version.claims[1];
    expect(interpClaim.claim_key).toBe("vb-backfill-run-1-1");
    expect(interpClaim.author).toBe("reader-agent");
    expect(interpClaim.rationale).toBe("文本意象解读。");
    expect(interpClaim.evidence_refs).toEqual([]);

    // 模型写出的哈希/血缘字段一律被投影覆盖（绝不采信）
    const forged = minimalModelContent();
    (forged as any).claims[0].claim_hash = "f".repeat(64);
    (forged as any).entities[0].stable_id = "forged-id";
    const replayed = projectVisualBibleVersion(forged, CTX, [
      SPAN_EVIDENCE,
    ]) as Record<string, any>;
    expect(replayed.claims[0].claim_hash).toBe(canonClaim.claim_hash);
    expect(replayed.entities[0].stable_id).toBe("stable-char-mu-shijing");
    expect(replayed.manifest_hash).toBe(version.manifest_hash);
  });

  it("manifest_hash / claim_hash 与后端 recompute 逐字节一致（黄金值）", () => {
    const version = projectVisualBibleVersion(minimalModelContent(), CTX, [
      SPAN_EVIDENCE,
    ]) as Record<string, any>;
    // 黄金值由后端 recompute_manifest_hash / claim_content_hash 生成
    // （agent-service/artifacts/vb-projection-golden.json 经后端
    // VisualBibleVersionContract + validate_version_contract 全门通过）。
    expect(version.manifest_hash).toBe(
      "ce1c2cd19ddcca51b800f65794f83e3c5f361e8e48f9e226318a76ee8f51ba75",
    );
    expect(version.claims[0].claim_hash).toBe(
      "b4384d056537b97dca8c0a3a7084a538db10c39703941a1a59cb774ed3ab91d6",
    );
    expect(version.claims[1].claim_hash).toBe(
      "aa4144857e08df567450f6e6a12c9eba1aeab3a8dcf67a2e6232e0eaee629937",
    );
  });

  it("fail closed：引用未物化的 evidence_key", () => {
    const content = minimalModelContent();
    content.claims[0].evidence_keys = ["qp:7:0:40:" + "9".repeat(64)];
    expect(() =>
      projectVisualBibleVersion(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/not materialized/);
  });

  it("fail closed：canon_fact claim 无 evidence_keys", () => {
    const content = minimalModelContent();
    content.claims[0].evidence_keys = [];
    expect(() =>
      projectVisualBibleVersion(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/canon_fact/);
  });

  it("fail closed：interpretation claim 缺 author/rationale", () => {
    const content = minimalModelContent();
    content.claims[1].author = "";
    expect(() =>
      projectVisualBibleVersion(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/author/);
  });

  it("fail closed：claim 引用未知 entity_key", () => {
    const content = minimalModelContent();
    content.claims[0].entity_key = "char-unknown";
    expect(() =>
      projectVisualBibleVersion(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/unknown entity/);
  });

  it("fail closed：evidence chapter 超 cutoff", () => {
    const content = minimalModelContent();
    expect(() =>
      projectVisualBibleVersion(content, { ...CTX, cutoffChapter: 1 }, [
        SPAN_EVIDENCE,
      ]),
    ).toThrow(/exceeds cutoff/);
  });

  it("fail closed：空 entities / 空 claims", () => {
    expect(() =>
      projectVisualBibleVersion(
        { entities: [], claims: minimalModelContent().claims },
        CTX,
        [SPAN_EVIDENCE],
      ),
    ).toThrow(/at least one entity/);
    expect(() =>
      projectVisualBibleVersion(
        { entities: minimalModelContent().entities, claims: [] },
        CTX,
        [SPAN_EVIDENCE],
      ),
    ).toThrow(/at least one claim/);
  });

  it("fail closed：非法 entity_type / authority 枚举", () => {
    const content = minimalModelContent();
    (content.entities[0] as any).entity_type = "dragon";
    expect(() =>
      projectVisualBibleVersion(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/entity_type/);
    const content2 = minimalModelContent();
    (content2.claims[0] as any).authority = "absolute_truth";
    expect(() =>
      projectVisualBibleVersion(content2, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/authority/);
  });

  it("fail closed：重复 entity_key", () => {
    const content = minimalModelContent();
    content.entities.push({ ...content.entities[0] });
    expect(() =>
      projectVisualBibleVersion(content, CTX, [SPAN_EVIDENCE]),
    ).toThrow(/duplicate entity_key/);
  });
});
