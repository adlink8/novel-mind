import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import {
  buildCitedAnswerEnvelope,
  type RunLineageContext,
} from "../src/structured-output/cited-answer-builder.js";

const CTX: RunLineageContext = {
  runId: "42",
  ownerId: 2,
  novelId: 6,
  skillVersionId: 7,
  inputHash: "a".repeat(64),
};

const SKILL = {
  name: "answer-reading-question",
  version: "1.0.0",
  allowedTools: [],
  instructions: "",
  filePath: "",
  baseDir: "",
  validateInput: () => true,
  validateOutput: () => true,
} as never;

function canonicalJson(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}
function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value !== null && typeof value === "object") {
    const rec = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(rec).sort()) out[k] = sortKeys(rec[k]);
    return out;
  }
  return value;
}
function sha256(s: string): string {
  return createHash("sha256").update(s, "utf8").digest("hex");
}

describe("cited-answer-builder", () => {
  it("produces a full envelope with required lineage fields", () => {
    const { envelope } = buildCitedAnswerEnvelope(
      "主角是林默。",
      CTX,
      SKILL,
      [{ toolName: "get_chapter", content: "第1章 雾中初见" }],
    );
    expect(envelope.type).toBe("cited_answer");
    expect(envelope.schema_version).toBe("cited-answer.v1");
    expect(envelope.owner_id).toBe(2);
    expect(envelope.novel_id).toBe(6);
    expect(envelope.skill_version_id).toBe(7);
    expect(envelope.input_hash).toBe(CTX.inputHash);
    expect(envelope.status).toBe("candidate");
    expect(envelope.evidence_refs).toEqual(["evidence:1"]);
    const answer = envelope.answer as {
      answer_blocks: Array<{ evidence_refs: string[] }>;
    };
    expect(answer.answer_blocks[0].evidence_refs).toEqual(["evidence:1"]);
    expect(envelope.producing_skill).toBe("answer-reading-question");
  });

  it("normalization repaired_hash replays against stripped payload", () => {
    const { envelope } = buildCitedAnswerEnvelope(
      "主角是林默。",
      CTX,
      SKILL,
      [{ toolName: "get_chapter", content: "第1章 雾中初见" }],
    );
    const stripped: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(envelope)) {
      if (k !== "normalization") stripped[k] = v;
    }
    const norm = envelope.normalization as {
      repaired_hash: string;
      raw_hash: string;
    };
    expect(norm.repaired_hash).toBe(sha256(canonicalJson(stripped)));
    expect(norm.raw_hash).toBeTruthy();
    expect(norm.raw_hash.length).toBe(64);
  });

  it("evidence_refs are deterministic per tool call count", () => {
    const a = buildCitedAnswerEnvelope("答", CTX, SKILL, [
      { toolName: "get_chapter", content: "c1" },
      { toolName: "search_novel_text", content: "s1" },
    ]);
    expect(a.envelope.evidence_refs).toEqual(["evidence:1", "evidence:2"]);
  });

  it("frozen manifest carries the same evidence allowlist", () => {
    const { frozenManifest } = buildCitedAnswerEnvelope("答", CTX, SKILL, [
      { toolName: "get_chapter", content: "c1" },
    ]);
    expect(frozenManifest).toEqual({ evidence_refs: ["evidence:1"] });
  });

  it("fails closed when no successful tool calls", () => {
    expect(() =>
      buildCitedAnswerEnvelope("答", CTX, SKILL, []),
    ).toThrow(/no successful read-only tool calls/);
  });

  it("fails closed on empty model text", () => {
    expect(() =>
      buildCitedAnswerEnvelope("", CTX, SKILL, [
        { toolName: "get_chapter", content: "c1" },
      ]),
    ).toThrow(/empty model output/);
  });
});
