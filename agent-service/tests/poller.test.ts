import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPoller, type PollerDeps } from "../src/poller.js";

const baseUrl = "http://127.0.0.1:8000";

function fakeSkill(name: string) {
  return {
    name,
    version: "1.0.0",
    allowedTools: [],
    instructions: "",
    filePath: "",
    baseDir: "",
    description: "",
    readPermissions: [],
    writePermissions: [],
    forbiddenSpaces: [],
    budget: {},
    approvalRequiredFor: [],
    inputSchema: {},
    outputSchema: {},
    validateInput: () => true,
    validateOutput: () => true,
  } as never;
}

interface FakeAssistantMsg {
  role: string;
  stopReason: string;
  content: Array<{ type: string; text?: string }>;
  provider?: string;
  model?: string;
  usage?: unknown;
}

/** 注入依赖，返回可控制的会话工厂。 */
function makePollerDeps(opts?: {
  lastStopReason?: string;
  lastText?: string;
  toolResults?: unknown[];
}): { deps: PollerDeps; session: { prompt: ReturnType<typeof vi.fn> } } {
  const assistant: FakeAssistantMsg = {
    role: "assistant",
    stopReason: opts?.lastStopReason ?? "stop",
    content: [{ type: "text", text: opts?.lastText ?? "分析结果" }],
    provider: "novelmind-gateway",
    model: "reader-chat-default",
    usage: { input: 0, output: 0 },
  };
  const messages = [...(opts?.toolResults ?? []), assistant];
  const session = {
    prompt: vi.fn(async () => undefined),
    messages,
    abort: vi.fn(async () => undefined),
    subscribe: vi.fn(() => () => undefined),
  };
  const deps: PollerDeps = {
    fetchImpl: vi.fn(async () => {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }),
    loadSkillImpl: vi.fn((name: string) => fakeSkill(name)),
    createSessionImpl: vi.fn(async () => session as never),
  };
  return { deps, session };
}

/** 安装后端 mock：queued-runs（有状态，claim 后返回空）→ claim → finalize。 */
function installBackendMock(
  fetchMock: ReturnType<typeof vi.fn>,
  opts?: { skillName?: string },
) {
  const skillName = opts?.skillName ?? "answer-reading-question";
  let queued = true;
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/queued-runs") && method === "GET") {
      if (!queued) {
        return Promise.resolve(
          new Response(JSON.stringify({ items: [], total: 0 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            items: [
              {
                run_id: 1,
                owner_id: 2,
                novel_id: 6,
                skill_version_id: 9,
                input: { novel_id: 6, question: "主角是谁" },
                input_hash: "a".repeat(64),
                branch: null,
                backfill_dimension: "raw_text",
              },
            ],
            total: 1,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    }
    if (url.endsWith("/claim") && method === "POST") {
      queued = false; // claim 后不再有 queued run
      return Promise.resolve(
        new Response(
          JSON.stringify({
            run_id: 1,
            owner_id: 2,
            novel_id: 6,
            skill_version_id: 9,
            skill_name: skillName,
            input: { novel_id: 6, question: "主角是谁" },
            input_hash: "a".repeat(64),
            branch: null,
            backfill_dimension: "raw_text",
            frozen_manifest: {},
            budget_snapshot: {},
            internal_token: "tok-backfill",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    }
    if (url.endsWith("/finalize") && method === "POST") {
      return Promise.resolve(
        new Response(
          JSON.stringify({ artifact: { id: 7, type: skillName, status: "candidate" } }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    }
    if (url.endsWith("/cancel") && method === "POST") {
      return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    }
    return Promise.resolve(
      new Response(JSON.stringify({ error: { code: "upstream_error" } }), { status: 502 }),
    );
  });
}

describe("createPoller", () => {
  beforeEach(() => {
    vi.stubEnv("NOVELMIND_GATEWAY_TOKEN", "test-gateway-token");
    vi.stubEnv("FASTAPI_BASE_URL", baseUrl);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("polls queued runs, claims, executes and finalizes", async () => {
    const { deps } = makePollerDeps({
      lastText: "林默是主角。",
      toolResults: [
        {
          role: "toolResult",
          toolName: "get_chapter",
          toolCallId: "c1",
          isError: false,
          content: [{ type: "text", text: "第1章 林默登场" }],
        },
      ],
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock);

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    // 等待第一轮 tick 完成。
    await new Promise((r) => setTimeout(r, 50));
    stop();

    const claims = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith("/claim") && (c[1] as RequestInit)?.method === "POST",
    );
    expect(claims.length).toBeGreaterThan(0);
    const finalizes = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith("/finalize") && (c[1] as RequestInit)?.method === "POST",
    );
    expect(finalizes.length).toBe(1);
    const finalizeBody = JSON.parse(String(finalizes[0][1]?.body ?? "{}"));
    expect(finalizeBody.stop_reason).toBe("stop");
    expect(finalizeBody.envelope.type).toBe("cited_answer");
    // claim token 用于认证。
    expect((finalizes[0][1]?.headers as Record<string, string>).authorization).toContain(
      "tok-backfill",
    );
  });

  it("claims only once (conflict on re-claim is ignored)", async () => {
    const { deps } = makePollerDeps();
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    let claimCalls = 0;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith("/queued-runs") && (init?.method ?? "GET") === "GET") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              items: [
                {
                  run_id: 1,
                  owner_id: 2,
                  novel_id: 6,
                  skill_version_id: 9,
                  input: { novel_id: 6, question: "q" },
                  input_hash: "a".repeat(64),
                  branch: null,
                  backfill_dimension: "raw_text",
                },
              ],
              total: 1,
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      if (url.endsWith("/claim")) {
        claimCalls += 1;
        return Promise.resolve(
          new Response(JSON.stringify({ error: { code: "conflict" } }), { status: 409 }),
        );
      }
      return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();
    // 409 被吞掉（不抛、不 cancel），也不 finalize。
    const finalizes = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith("/finalize"),
    );
    expect(finalizes.length).toBe(0);
  });

  // ══════════════════════════════════════════════════════════════════
  // Phase 40 Bug P1：分析 skill 必须按 skill 构造对应 envelope.type，
  // 绝不能 fallback 成 cited_answer（后端 integrity 恒 BLOCKED_NO_EVIDENCE）。
  // ══════════════════════════════════════════════════════════════════

  function finalizeCalls(fetchMock: ReturnType<typeof vi.fn>) {
    return fetchMock.mock.calls
      .filter(
        (c) => String(c[0]).endsWith("/finalize") && (c[1] as RequestInit)?.method === "POST",
      )
      .map((c) => JSON.parse(String((c[1] as RequestInit)?.body ?? "{}")));
  }

  function cancelCalls(fetchMock: ReturnType<typeof vi.fn>) {
    return fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith("/cancel") && (c[1] as RequestInit)?.method === "POST",
    );
  }

  it("detect-key-scenes backfill finalizes scene_candidate envelope with evidence_refs", async () => {
    const sceneSet = {
      schema_version: "key-scene.v1",
      artifact_kind: "key_scene",
      owner_id: 2,
      novel_id: 6,
      version_key: "ks-main",
      revision_number: 1,
      source_snapshot_id: "ss-1",
      source_snapshot_hash: "b".repeat(64),
      cutoff_chapter: 1,
      schema_hash: "c".repeat(64),
      policy_hash: "d".repeat(64),
      detector_id: "key-scene.v1",
      detector_version: "1.0.0",
      manifest_hash: "e".repeat(64),
      candidates: [
        {
          candidate_key: "ks-0",
          candidate_order: 0,
          scene_id: "scene-0",
          chapter_id: 1,
          chapter_number: 1,
          source_start: 0,
          source_end: 40,
          source_hash: "f".repeat(64),
          coordinates: { cast: ["arin"], place: "courtyard", time: "night", pov: "arin" },
          spoiler_cutoff: 1,
          salience_reasons: [{ reason_code: "plot_turn", detail: "attack", score: 0.9 }],
          score_total: 0.9,
          score_breakdown: { action: 0.8 },
          diversity_key: "dk-0",
          detector_id: "key-scene.v1",
          detector_version: "1.0.0",
          policy_hash: "d".repeat(64),
          evidence_ranges: [
            {
              evidence_key: "qp:1:0:40:1111111111111111111111111111111111111111111111111111111111111111",
              source_snapshot_id: "ss-1",
              source_snapshot_hash: "b".repeat(64),
              chapter_id: 1,
              chapter_number: 1,
              source_start: 0,
              source_end: 40,
              content_hash: "f".repeat(64),
              cutoff_chapter: 1,
            },
          ],
          review_state: "candidate",
        },
      ],
      review_state: "candidate",
    };
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "scene_candidate",
        schema_version: "scene-candidate.v1",
        scene_candidate_set: sceneSet,
        tool_runs: [{ tool_name: "get_evidence_span", calls: 1 }],
      }),
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "detect-key-scenes" });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    const finals = finalizeCalls(fetchMock);
    expect(finals.length).toBe(1);
    expect(finals[0].stop_reason).toBe("stop");
    expect(finals[0].envelope.type).toBe("scene_candidate");
    expect(finals[0].envelope.schema_version).toBe("scene-candidate.v1");
    expect(finals[0].envelope.scene_candidate_set.version_key).toBe("ks-main");
    expect(finals[0].envelope.evidence_refs).toEqual([
      "qp:1:0:40:1111111111111111111111111111111111111111111111111111111111111111",
    ]);
    expect(finals[0].frozen_manifest.evidence_refs).toEqual(finals[0].envelope.evidence_refs);
    expect(finals[0].envelope.normalization.repaired_hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it("propose-world-model-candidates backfill finalizes world_model_candidate envelope", async () => {
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "world_model_candidate",
        schema_version: "world-model-candidate.v1",
        candidates: {
          projection_version: 1,
          tool_runs: [{ tool_name: "get_events", calls: 2 }],
          claims: [
            {
              claim_kind: "character_state",
              claim_key: "cs-1",
              proposition: "林安的目标是找到使者。",
              subject: "林安",
              authority: "literary_interpretation",
              confidence: 0.7,
              disclosure_cutoff: 1,
              evidence_refs: [
                "qp:1:0:40:1111111111111111111111111111111111111111111111111111111111111111",
              ],
            },
          ],
        },
      }),
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "propose-world-model-candidates" });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    const finals = finalizeCalls(fetchMock);
    expect(finals.length).toBe(1);
    expect(finals[0].envelope.type).toBe("world_model_candidate");
    expect(finals[0].envelope.schema_version).toBe("world-model-candidate.v1");
    expect(finals[0].envelope.candidates.claims.length).toBe(1);
    expect(finals[0].envelope.evidence_refs).toEqual([
      "qp:1:0:40:1111111111111111111111111111111111111111111111111111111111111111",
    ]);
  });

  it("build-visual-bible backfill finalizes visual_bible envelope", async () => {
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "visual_bible",
        schema_version: "visual-bible.v1",
        visual_bible: {
          schema_version: "visual-bible.v1",
          artifact_kind: "visual_bible",
          owner_id: 2,
          novel_id: 6,
          version_key: "vb-main",
          revision_number: 1,
          source_snapshot_id: "ss-1",
          source_snapshot_hash: "b".repeat(64),
          cutoff_chapter: 1,
          schema_hash: "c".repeat(64),
          policy_hash: "d".repeat(64),
          manifest_hash: "e".repeat(64),
          entities: [
            {
              stable_id: "char-ayla",
              entity_key: "char-ayla",
              entity_type: "character",
              description: "amber hair",
              authority: "canon_fact",
              disclosure_cutoff: 1,
            },
          ],
          claims: [
            {
              claim_key: "char-ayla-hair",
              entity_stable_id: "char-ayla",
              authority: "canon_fact",
              description: "amber braided hair",
              cutoff_chapter: 1,
              claim_hash: "g".repeat(64),
              evidence_refs: [
                {
                  evidence_key: "qp:1:0:40:1111111111111111111111111111111111111111111111111111111111111111",
                  source_snapshot_id: "ss-1",
                  source_snapshot_hash: "b".repeat(64),
                  chapter_id: 1,
                  chapter_number: 1,
                  source_start: 0,
                  source_end: 40,
                  content_hash: "f".repeat(64),
                  cutoff_chapter: 1,
                },
              ],
            },
          ],
          review_state: "candidate",
        },
        tool_runs: [{ tool_name: "get_visual_bible", calls: 1 }],
      }),
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "build-visual-bible" });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    const finals = finalizeCalls(fetchMock);
    expect(finals.length).toBe(1);
    expect(finals[0].envelope.type).toBe("visual_bible");
    expect(finals[0].envelope.schema_version).toBe("visual-bible.v1");
    expect(finals[0].envelope.visual_bible.version_key).toBe("vb-main");
    expect(finals[0].envelope.evidence_refs).toEqual([
      "qp:1:0:40:1111111111111111111111111111111111111111111111111111111111111111",
    ]);
  });

  it("analysis skill with non-JSON model output fails honestly (no finalize, cancel)", async () => {
    const { deps } = makePollerDeps({ lastText: "这是一段普通文本，不是 JSON。" });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "detect-key-scenes" });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    expect(finalizeCalls(fetchMock).length).toBe(0);
    expect(cancelCalls(fetchMock).length).toBeGreaterThan(0);
  });

  it("analysis skill with no leaf evidence refs fails honestly (no finalize, cancel)", async () => {
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "scene_candidate",
        schema_version: "scene-candidate.v1",
        scene_candidate_set: {
          schema_version: "key-scene.v1",
          artifact_kind: "key_scene",
          candidates: [{ candidate_key: "ks-0", evidence_ranges: [] }],
          review_state: "candidate",
        },
        tool_runs: [{ tool_name: "get_evidence_span", calls: 1 }],
      }),
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "detect-key-scenes" });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    expect(finalizeCalls(fetchMock).length).toBe(0);
    expect(cancelCalls(fetchMock).length).toBeGreaterThan(0);
  });
});
