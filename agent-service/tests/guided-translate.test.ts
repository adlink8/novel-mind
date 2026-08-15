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
    const out = JSON.parse(translateEvidenceIndices(modelJson([1, 2]), KEYS));
    const claim = out.visual_bible.claims[0];
    expect(claim.evidence_keys).toEqual(KEYS);
    expect(claim.evidence_indices).toBeUndefined();
  });

  it("乱序/重复编号保持模型意图", () => {
    const out = JSON.parse(translateEvidenceIndices(modelJson([2, 2]), KEYS));
    expect(out.visual_bible.claims[0].evidence_keys).toEqual([KEYS[1], KEYS[1]]);
  });

  it("编号越界 → fail closed", () => {
    expect(() => translateEvidenceIndices(modelJson([3]), KEYS)).toThrow(
      /out of range/,
    );
    expect(() => translateEvidenceIndices(modelJson([0]), KEYS)).toThrow(
      /out of range/,
    );
    expect(() => translateEvidenceIndices(modelJson([-1]), KEYS)).toThrow(
      /out of range/,
    );
  });

  it("非整数编号 → fail closed", () => {
    expect(() => translateEvidenceIndices(modelJson(["1"]), KEYS)).toThrow(
      /integer/,
    );
    expect(() => translateEvidenceIndices(modelJson([1.5]), KEYS)).toThrow(
      /integer/,
    );
  });

  it("缺失 evidence_indices 的 claim 原样保留（投影层负责后续校验）", () => {
    const text = modelJson(undefined);
    const out = JSON.parse(translateEvidenceIndices(text, KEYS));
    expect(out.visual_bible.claims[0].evidence_keys).toBeUndefined();
  });

  it("非 JSON 输入 → fail closed", () => {
    expect(() => translateEvidenceIndices("不是 JSON", KEYS)).toThrow();
  });
});
