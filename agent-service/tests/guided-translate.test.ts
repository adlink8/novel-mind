/**
 * guided 编号→key 翻译层测试（Slice 2）。
 *
 * 模型输出 claims[].evidence_indices（菜单编号，1-based）；程序映射回
 * 真实 evidence_key。越界/非整数编号 → fail closed（进 repair 轮）。
 * 映射后的 JSON 与 agentic 路径的模型输出同构，投影层零改动复用。
 */

import { describe, it, expect } from "vitest";
import { translateEvidenceIndices } from "../src/guided/translate.js";

const KEYS = ["qp:7:0:10:" + "a".repeat(64), "qp:8:5:20:" + "b".repeat(64)];

function modelJson(indices: unknown): string {
  return JSON.stringify({
    visual_bible: {
      entities: [
        {
          entity_key: "place-dead-city",
          entity_type: "place",
          description: "死城",
          authority: "canon_fact",
        },
      ],
      claims: [
        {
          entity_key: "place-dead-city",
          authority: "canon_fact",
          description: "死城布满眼球状凸起",
          evidence_indices: indices,
        },
      ],
    },
  });
}

describe("translateEvidenceIndices", () => {
  it("编号映射为真实 evidence_key，evidence_indices 字段移除", () => {
    const out = JSON.parse(translateEvidenceIndices(modelJson([1, 2]), KEYS, "build-visual-bible"));
    const claim = out.visual_bible.claims[0];
    expect(claim.evidence_keys).toEqual(KEYS);
    expect(claim.evidence_indices).toBeUndefined();
  });

  it("乱序/重复编号保持模型意图", () => {
    const out = JSON.parse(translateEvidenceIndices(modelJson([2, 2]), KEYS, "build-visual-bible"));
    expect(out.visual_bible.claims[0].evidence_keys).toEqual([KEYS[1], KEYS[1]]);
  });

  it("编号越界 → fail closed", () => {
    expect(() => translateEvidenceIndices(modelJson([3]), KEYS, "build-visual-bible")).toThrow(
      /out of range/,
    );
    expect(() => translateEvidenceIndices(modelJson([0]), KEYS, "build-visual-bible")).toThrow(
      /out of range/,
    );
    expect(() => translateEvidenceIndices(modelJson([-1]), KEYS, "build-visual-bible")).toThrow(
      /out of range/,
    );
  });

  it("非整数编号 → fail closed", () => {
    expect(() => translateEvidenceIndices(modelJson(["1"]), KEYS, "build-visual-bible")).toThrow(
      /integer/,
    );
    expect(() => translateEvidenceIndices(modelJson([1.5]), KEYS, "build-visual-bible")).toThrow(
      /integer/,
    );
  });

  it("缺失 evidence_indices 的 claim 原样保留（投影层负责后续校验）", () => {
    const text = modelJson(undefined);
    const out = JSON.parse(translateEvidenceIndices(text, KEYS, "build-visual-bible"));
    expect(out.visual_bible.claims[0].evidence_keys).toBeUndefined();
  });

  it("非 JSON 输入 → fail closed", () => {
    expect(() => translateEvidenceIndices("不是 JSON", KEYS, "build-visual-bible")).toThrow();
  });
});


const SCENE_KEY = "qp:1:0:40:" + "1".repeat(64);

describe("translateEvidenceIndices — detect-key-scenes", () => {
  const sceneJson = (indices: unknown) =>
    JSON.stringify({
      scene_candidate_set: {
        candidates: [
          {
            evidence_indices: indices,
            coordinates: { cast: ["林守溪"], place: "城门" },
            salience_reasons: [{ reason_code: "plot_turn", detail: "追杀", score: 0.9 }],
          },
        ],
      },
    });

  it("恰好 1 个编号 → 单数 evidence_key", () => {
    const out = JSON.parse(
      translateEvidenceIndices(sceneJson([1]), [SCENE_KEY], "detect-key-scenes"),
    );
    const candidate = out.scene_candidate_set.candidates[0];
    expect(candidate.evidence_key).toBe(SCENE_KEY);
    expect(candidate.evidence_indices).toBeUndefined();
  });

  it("多个编号 → fail closed（每个候选只能挂 1 条证据）", () => {
    expect(() =>
      translateEvidenceIndices(sceneJson([1, 1]), [SCENE_KEY], "detect-key-scenes"),
    ).toThrow(/exactly one/);
  });
});

describe("translateEvidenceIndices — propose-world-model-candidates", () => {
  const wmcJson = (indices: unknown) =>
    JSON.stringify({
      candidates: {
        projection_version: 1,
        claims: [
          {
            claim_kind: "character_state",
            claim_key: "cs-1",
            proposition: "林守溪此刻在城中",
            subject: "林守溪",
            authority: "canon_fact",
            confidence: 0.9,
            disclosure_cutoff: 1,
            evidence_indices: indices,
          },
        ],
      },
    });

  it("编号数组 → evidence_refs 字符串 key 数组", () => {
    const out = JSON.parse(
      translateEvidenceIndices(wmcJson([1]), [SCENE_KEY], "propose-world-model-candidates"),
    );
    const claim = out.candidates.claims[0];
    expect(claim.evidence_refs).toEqual([SCENE_KEY]);
    expect(claim.evidence_indices).toBeUndefined();
  });

  it("越界编号 → fail closed", () => {
    expect(() =>
      translateEvidenceIndices(wmcJson([2]), [SCENE_KEY], "propose-world-model-candidates"),
    ).toThrow(/out of range/);
  });
});
