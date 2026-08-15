import { describe, expect, it } from "vitest";
import {
  extractRuntimeToolEvidence,
  type RuntimeToolRunSummary,
} from "../src/tools/tool-evidence.js";
import { buildAnalysisEnvelope } from "../src/structured-output/analysis-envelope-builder.js";

const CTX = {
  runId: "run-1",
  ownerId: 7,
  novelId: 3,
  skillVersionId: 42,
  inputHash: "b".repeat(64),
};

const SKILL = {
  name: "propose-world-model-candidates",
  version: "1.0.0",
  allowedTools: ["get_events"],
} as never;

describe("runtime tool evidence", () => {
  it("counts only runtime tool results, sorts names, and excludes args/content", () => {
    const messages = [
      {
        role: "assistant",
        content: [
          {
            type: "toolCall",
            name: "get_chapter",
            arguments: { secret: "do-not-persist" },
          },
        ],
        tool_runs: [{ tool_name: "model_claim", calls: 99, errors: 0 }],
      },
      {
        role: "toolResult",
        toolName: "get_chapter",
        isError: false,
        content: [{ type: "text", text: "secret novel content" }],
      },
      {
        role: "toolResult",
        toolName: "get_chapter",
        isError: true,
        content: [{ type: "text", text: "private error details" }],
      },
      {
        role: "toolResult",
        toolName: "get_events",
        isError: false,
        content: [{ type: "text", text: "event content" }],
      },
    ];

    const snapshot = extractRuntimeToolEvidence(messages, ["get_events", "get_chapter"]);

    expect(snapshot.toolRuns).toEqual<RuntimeToolRunSummary[]>([
      { tool_name: "get_chapter", calls: 2, errors: 1 },
      { tool_name: "get_events", calls: 1, errors: 0 },
    ]);
    expect(snapshot.successfulEvidences).toEqual([
      { toolName: "get_chapter", content: "secret novel content" },
      { toolName: "get_events", content: "event content" },
    ]);
    expect(JSON.stringify(snapshot.toolRuns)).not.toContain("secret");
    expect(JSON.stringify(snapshot.toolRuns)).not.toContain("private");
  });

  it("rejects a runtime result outside the Skill allowed_tools", () => {
    expect(() =>
      extractRuntimeToolEvidence(
        [{ role: "toolResult", toolName: "not_allowed", isError: true, content: [] }],
        ["get_chapter"],
      ),
    ).toThrow(/allowed_tools|not_allowed/);
  });

  it("uses runtime summaries for Artifact tool_runs, never model-claimed tool_runs", () => {
    const modelOutput = JSON.stringify({
      type: "world_model_candidate",
      schema_version: "world-model-candidate.v1",
      candidates: {
        projection_version: 1,
        tool_runs: [{ tool_name: "model_claim", calls: 99, errors: 0 }],
        claims: [
          {
            claim_kind: "character_state",
            claim_key: "cs-1",
            proposition: "林默的目标是找到使者。",
            subject: "林默",
            authority: "literary_interpretation",
            confidence: 0.7,
            disclosure_cutoff: 1,
            evidence_refs: ["qp:1:0:40:" + "1".repeat(64)],
          },
        ],
      },
    });
    const runtimeToolRuns = [{ tool_name: "get_events", calls: 2, errors: 1 }];
    // Slice B 选择制：claim 引用的 ref 必须由运行时 get_evidence_span 物化。
    const runtimeEvidences = [
      {
        toolName: "get_evidence_span",
        content: JSON.stringify({
          evidence_key: "qp:1:0:40:" + "1".repeat(64),
          chapter_id: 1,
          chapter_number: 1,
          novel_id: 3,
          source_start: 0,
          source_end: 40,
          content_hash: "1".repeat(64),
          excerpt: "林默出发寻找使者",
        }),
      },
    ];

    const result = buildAnalysisEnvelope(
      modelOutput,
      CTX,
      SKILL,
      null,
      runtimeToolRuns,
      runtimeEvidences,
    );

    expect(result.envelope.tool_runs).toEqual(runtimeToolRuns);
    expect(result.frozenManifest.tool_runs).toEqual(runtimeToolRuns);
    expect(JSON.stringify(result.envelope)).not.toContain("model_claim");
  });

  it("rejects an artifact summary that escapes the Skill allowed_tools", () => {
    expect(() =>
      buildAnalysisEnvelope(
        JSON.stringify({
          candidates: {
            claims: [{ evidence_refs: ["qp:1:0:40:" + "1".repeat(64)] }],
          },
        }),
        CTX,
        SKILL,
        null,
        [{ tool_name: "get_chapter", calls: 1, errors: 0 }],
      ),
    ).toThrow(/allowed_tools/);
  });
});
