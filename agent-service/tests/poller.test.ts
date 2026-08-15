import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPoller, type PollerDeps } from "../src/poller.js";

const baseUrl = "http://127.0.0.1:8000";

function fakeSkill(name: string) {
  return {
    name,
    version: "1.0.0",
    // 运行时摘要只认 toolResult；fixture 明确声明它可能实际调用的域工具。
    allowedTools: [
      "get_chapter",
      "search_novel_text",
      "get_evidence_span",
      "get_events",
      "get_visual_bible",
    ],
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
  emitToolStarts?: number;
  repairTexts?: string[];
}): {
  deps: PollerDeps;
  session: {
    prompt: ReturnType<typeof vi.fn>;
    abort: ReturnType<typeof vi.fn>;
  };
  createSession: ReturnType<typeof vi.fn>;
} {
  const assistant: FakeAssistantMsg = {
    role: "assistant",
    stopReason: opts?.lastStopReason ?? "stop",
    content: [{ type: "text", text: opts?.lastText ?? "分析结果" }],
    provider: "novelmind-gateway",
    model: "reader-chat-default",
    usage: { input: 0, output: 0 },
  };
  const messages = [...(opts?.toolResults ?? []), assistant];
  let listener: ((event: { type?: string }) => void) | undefined;
  let promptCalls = 0;
  const session = {
    prompt: vi.fn(async () => {
      promptCalls += 1;
      const n = opts?.emitToolStarts ?? 0;
      for (let k = 0; k < n; k += 1) listener?.({ type: "tool_execution_start" });
      // repair 轮：第 2+ 次 prompt 把下一条修复输出追加进 transcript
      const repairTexts = opts?.repairTexts ?? [];
      if (promptCalls > 1 && repairTexts.length > 0) {
        const text = repairTexts.shift()!;
        messages.push({
          role: "assistant",
          stopReason: "stop",
          content: [{ type: "text", text }],
          provider: "novelmind-gateway",
          model: "reader-chat-default",
          usage: { input: 0, output: 0 },
        });
      }
    }),
    messages,
    abort: vi.fn(async () => undefined),
    subscribe: vi.fn((fn: (event: { type?: string }) => void) => {
      listener = fn;
      return () => undefined;
    }),
  };
  const createSession = vi.fn(async () => session as never);
  const deps: PollerDeps = {
    fetchImpl: vi.fn(async () => {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }),
    loadSkillImpl: vi.fn((name: string) => fakeSkill(name)),
    createSessionImpl: createSession,
  };
  return { deps, session, createSession };
}

/** 安装后端 mock：queued-runs（有状态，claim 后返回空）→ claim → finalize。 */
function installBackendMock(
  fetchMock: ReturnType<typeof vi.fn>,
  opts?: {
    skillName?: string;
    origin?: "chat_backfill" | "reader_chat";
    budgetSnapshot?: Record<string, unknown>;
  },
) {
  const skillName = opts?.skillName ?? "answer-reading-question";
  const origin = opts?.origin ?? "chat_backfill";
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
                input:
                  origin === "reader_chat"
                    ? {
                        novel_id: 6,
                        question: "主角是谁",
                        preference_context: {
                          items: [
                            { memory_id: 12, kind: "response_style", value: "concise" },
                          ],
                          memory_ids: [12],
                        },
                      }
                    : { novel_id: 6, question: "主角是谁" },
                input_hash: "a".repeat(64),
                branch: null,
                backfill_dimension: "raw_text",
                origin,
                user_message_id: origin === "reader_chat" ? 77 : null,
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
            input: {
              novel_id: 6,
              question: "主角是谁",
              ...(skillName === "analyze-chapter"
                ? { chapter_id: 1, chapter_number: 1, cutoff_chapter: 10 }
                : {}),
              // Slice A / P1a：detect-key-scenes 与 build-visual-bible 的
              // run input 由后端锚定 source snapshot hash + cutoff
              // （程序产出，模型不参与）。
              ...(skillName === "detect-key-scenes" ||
              skillName === "build-visual-bible"
                ? {
                    source_snapshot: { snapshot_hash: "b".repeat(64) },
                    cutoff_chapter: 1,
                  }
                : {}),
              preference_context: {
                items: [{ memory_id: 12, kind: "response_style", value: "concise" }],
                memory_ids: [12],
              },
            },
            input_hash: "a".repeat(64),
            branch: null,
            backfill_dimension: "raw_text",
            origin,
            user_message_id: origin === "reader_chat" ? 77 : null,
            frozen_manifest:
              origin === "reader_chat" ? { evidence_refs: ["selection:1"] } : {},
            budget_snapshot: opts?.budgetSnapshot ?? {},
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

  it("polls ordinary reader_chat runs and preserves their frozen reader refs", async () => {
    const { deps, createSession } = makePollerDeps({
      lastText: "林默是主角。",
      toolResults: [
        {
          role: "toolResult",
          toolName: "search_novel_text",
          isError: false,
          content: [{ type: "text", text: "林默登场" }],
        },
      ],
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { origin: "reader_chat" });

    const stop = createPoller(deps, [], { intervalMs: 10 }).start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    const finalize = fetchMock.mock.calls.find(
      (c) => String(c[0]).endsWith("/finalize") && (c[1] as RequestInit)?.method === "POST",
    );
    expect(finalize).toBeDefined();
    const body = JSON.parse(String((finalize?.[1] as RequestInit)?.body ?? "{}"));
    expect(body.envelope.evidence_refs).toEqual(["selection:1"]);
    expect(body.frozen_manifest.evidence_refs).toEqual(["selection:1"]);
    const sessionOptions = createSession.mock.calls[0][0] as { systemContext?: string };
    expect(sessionOptions.systemContext).toContain("response_style=concise");
    expect(sessionOptions.systemContext).toContain("memory_id=12");
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
    // Slice A：模型只产出语义字段 + 从 get_evidence_span 结果里选择的
    // evidence_key；全部哈希/血缘字段由 builder 投影注入。
    const spanHash = "1".repeat(64);
    const evidenceKey = `qp:1:0:40:${spanHash}`;
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "scene_candidate",
        schema_version: "scene-candidate.v1",
        scene_candidate_set: {
          candidates: [
            {
              evidence_key: evidenceKey,
              coordinates: { cast: ["arin"], place: "courtyard" },
              salience_reasons: [
                { reason_code: "plot_turn", detail: "attack", score: 0.9 },
              ],
              score_total: 0.9,
              score_breakdown: { action: 0.8 },
            },
          ],
        },
      }),
      toolResults: [
        {
          role: "toolResult",
          toolName: "get_evidence_span",
          isError: false,
          content: [
            {
              type: "text",
              text: JSON.stringify({
                evidence_key: evidenceKey,
                chapter_id: 1,
                chapter_number: 1,
                novel_id: 6,
                source_start: 0,
                source_end: 40,
                content_hash: spanHash,
                excerpt: "courtyard attack",
              }),
            },
          ],
        },
      ],
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
    const set = finals[0].envelope.scene_candidate_set;
    expect(set.version_key).toBe("ks-backfill-run-1");
    expect(set.source_snapshot_hash).toBe("b".repeat(64));
    expect(set.cutoff_chapter).toBe(1);
    expect(set.schema_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(set.policy_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(set.manifest_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(set.candidates[0].source_hash).toBe(spanHash);
    expect(set.candidates[0].evidence_ranges[0].evidence_key).toBe(evidenceKey);
    expect(finals[0].envelope.evidence_refs).toEqual([evidenceKey]);
    expect(finals[0].frozen_manifest.evidence_refs).toEqual(finals[0].envelope.evidence_refs);
    expect(finals[0].envelope.normalization.repaired_hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it("propose-world-model-candidates backfill finalizes world_model_candidate envelope", async () => {
    // Slice B：claim 引用的 evidence_ref 必须来自运行时 get_evidence_span
    // 物化结果（选择制），编造的 key fail closed。
    const spanHash = "1".repeat(64);
    const evidenceKey = `qp:1:0:40:${spanHash}`;
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
              evidence_refs: [evidenceKey],
            },
          ],
        },
      }),
      toolResults: [
        {
          role: "toolResult",
          toolName: "get_evidence_span",
          isError: false,
          content: [
            {
              type: "text",
              text: JSON.stringify({
                evidence_key: evidenceKey,
                chapter_id: 1,
                chapter_number: 1,
                novel_id: 6,
                source_start: 0,
                source_end: 40,
                content_hash: spanHash,
                excerpt: "林安出发寻找使者",
              }),
            },
          ],
        },
      ],
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
    expect(finals[0].envelope.evidence_refs).toEqual([evidenceKey]);
  });

  it("信封构建失败时把错误反馈到同一 session 修复（有界 repair loop）", async () => {
    // Slice C：第一次模型输出引用了未物化的 ref（构建 fail closed），
    // poller 把校验错误喂回同一 pi session；第二轮模型选中合法 key → 成功。
    const spanHash = "1".repeat(64);
    const evidenceKey = `qp:1:0:40:${spanHash}`;
    const badOutput = JSON.stringify({
      type: "world_model_candidate",
      schema_version: "world-model-candidate.v1",
      candidates: {
        projection_version: 1,
        tool_runs: [],
        claims: [
          {
            claim_kind: "character_state",
            claim_key: "cs-1",
            proposition: "编造引用的主张。",
            subject: "林安",
            authority: "literary_interpretation",
            confidence: 0.7,
            disclosure_cutoff: 1,
            evidence_refs: ["qp:1:0:40:" + "9".repeat(64)],
          },
        ],
      },
    });
    const goodOutput = JSON.stringify({
      type: "world_model_candidate",
      schema_version: "world-model-candidate.v1",
      candidates: {
        projection_version: 1,
        tool_runs: [],
        claims: [
          {
            claim_kind: "character_state",
            claim_key: "cs-1",
            proposition: "林安的目标是找到使者。",
            subject: "林安",
            authority: "literary_interpretation",
            confidence: 0.7,
            disclosure_cutoff: 1,
            evidence_refs: [evidenceKey],
          },
        ],
      },
    });
    const { deps, session } = makePollerDeps({
      lastText: badOutput,
      repairTexts: [goodOutput],
      toolResults: [
        {
          role: "toolResult",
          toolName: "get_evidence_span",
          isError: false,
          content: [
            {
              type: "text",
              text: JSON.stringify({
                evidence_key: evidenceKey,
                chapter_id: 1,
                chapter_number: 1,
                novel_id: 6,
                source_start: 0,
                source_end: 40,
                content_hash: spanHash,
                excerpt: "林安出发寻找使者",
              }),
            },
          ],
        },
      ],
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, {
      skillName: "propose-world-model-candidates",
    });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    expect(session.prompt).toHaveBeenCalledTimes(2);
    // 修复 prompt 必须携带校验错误清单（供模型定向修正）
    const repairPrompt = String(session.prompt.mock.calls[1][0]);
    expect(repairPrompt).toContain("not materialized");
    // 选择制闭环：修复 prompt 必须携带已物化 evidence key 菜单
    expect(repairPrompt).toContain("Available materialized evidence keys");
    expect(repairPrompt).toContain(evidenceKey);
    const finals = finalizeCalls(fetchMock);
    expect(finals.length).toBe(1);
    expect(finals[0].stop_reason).toBe("stop");
    expect(finals[0].envelope.evidence_refs).toEqual([evidenceKey]);
  });

  it("repair 轮数耗尽（1 + 2 次修复）→ failed 终态，不无限重试", async () => {
    const badOutput = JSON.stringify({
      type: "world_model_candidate",
      schema_version: "world-model-candidate.v1",
      candidates: {
        projection_version: 1,
        tool_runs: [],
        claims: [
          {
            claim_kind: "character_state",
            claim_key: "cs-1",
            proposition: "始终编造引用。",
            subject: "林安",
            authority: "literary_interpretation",
            confidence: 0.7,
            disclosure_cutoff: 1,
            evidence_refs: ["qp:1:0:40:" + "9".repeat(64)],
          },
        ],
      },
    });
    const { deps, session } = makePollerDeps({
      lastText: badOutput,
      repairTexts: [badOutput, badOutput, badOutput],
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, {
      skillName: "propose-world-model-candidates",
    });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    expect(session.prompt).toHaveBeenCalledTimes(3);
    const finals = finalizeCalls(fetchMock);
    expect(finals.length).toBe(1);
    expect(finals[0].stop_reason).toBe("error");
  });

    it("world_model_candidate 引用未物化的 evidence_ref → fail closed", async () => {
    // Slice B：编造的 evidence_ref（无对应 get_evidence_span 工具结果）
    // 必须让 run 进入 failed 终态，绝不写入信封。
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "world_model_candidate",
        schema_version: "world-model-candidate.v1",
        candidates: {
          projection_version: 1,
          tool_runs: [{ tool_name: "get_events", calls: 1 }],
          claims: [
            {
              claim_kind: "character_state",
              claim_key: "cs-1",
              proposition: "编造引用的主张。",
              subject: "林安",
              authority: "literary_interpretation",
              confidence: 0.7,
              disclosure_cutoff: 1,
              evidence_refs: ["qp:1:0:40:" + "9".repeat(64)],
            },
          ],
        },
      }),
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, {
      skillName: "propose-world-model-candidates",
    });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    const finals = finalizeCalls(fetchMock);
    expect(finals.length).toBe(1);
    expect(finals[0].stop_reason).toBe("error");
  });

  it("analysis skill with non-JSON model output reaches a failed terminal state", async () => {
    const { deps } = makePollerDeps({ lastText: "这是一段普通文本，不是 JSON。" });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "detect-key-scenes" });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 50));
    stop();

    expect(finalizeCalls(fetchMock)).toContainEqual(
      expect.objectContaining({ stop_reason: "error", envelope: {} }),
    );
    expect(cancelCalls(fetchMock).length).toBe(0);
  });

  it("analysis skill with no leaf evidence refs reaches a failed terminal state", async () => {
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

    expect(finalizeCalls(fetchMock)).toContainEqual(
      expect.objectContaining({ stop_reason: "error", envelope: {} }),
    );
    expect(cancelCalls(fetchMock).length).toBe(0);
  });

  it("analyze-chapter finalizes a chapter_analysis envelope", async () => {
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "chapter_analysis",
        schema_version: "chapter-analysis.v1",
        evidence_refs: ["evidence:chapter:1"],
        analysis: {
          schema_version: "chapter-analysis-artifact.v1",
          chapter_id: 1,
          chapter_number: 1,
          source_snapshot_hash: "a".repeat(64),
          input_hash: "b".repeat(64),
          cutoff: 10,
          max_length: 2000,
          spoiler_policy_version: "spoiler-policy.v1",
          chapter_digest: "c".repeat(64),
          chunk_digests: ["d".repeat(64)],
          previous_context_summary: null,
          next_context_hint: null,
          next_hint_reason_code: null,
          continuity_notes: "第一章建立主角处境。",
        },
      }),
      toolResults: [
        {
          role: "toolResult",
          toolName: "get_chapter",
          toolCallId: "chapter-1",
          isError: false,
          content: [{ type: "text", text: "第一章正文" }],
        },
      ],
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "analyze-chapter" });

    const stop = createPoller(deps, [], { intervalMs: 10 }).start();
    await new Promise((resolve) => setTimeout(resolve, 50));
    stop();

    const finals = finalizeCalls(fetchMock);
    expect(finals).toHaveLength(1);
    expect(finals[0].stop_reason).toBe("stop");
    expect(finals[0].envelope.type).toBe("chapter_analysis");
    expect(finals[0].envelope.analysis.chapter_id).toBe(1);
    expect(finals[0].envelope.evidence_refs).toEqual(["evidence:1"]);
  });

  it("analyze-chapter accepts one fenced JSON object with surrounding prose", async () => {
    const payload = {
      type: "chapter_analysis",
      schema_version: "chapter-analysis.v1",
      analysis: {
        schema_version: "chapter-analysis-artifact.v1",
        chapter_id: 1,
        chapter_number: 1,
        source_snapshot_hash: "a".repeat(64),
        input_hash: "b".repeat(64),
        cutoff: 10,
        max_length: 2000,
        spoiler_policy_version: "spoiler-policy.v1",
        chapter_digest: "c".repeat(64),
        chunk_digests: ["d".repeat(64)],
        previous_context_summary: null,
        next_context_hint: null,
        next_hint_reason_code: null,
        continuity_notes: "第一章建立主角处境。",
      },
    };
    const { deps } = makePollerDeps({
      lastText: `分析完成。\n\n\`\`\`json\n${JSON.stringify(payload)}\n\`\`\``,
      toolResults: [
        {
          role: "toolResult",
          toolName: "get_chapter",
          toolCallId: "chapter-1",
          isError: false,
          content: [{ type: "text", text: "第一章正文" }],
        },
      ],
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "analyze-chapter" });

    const stop = createPoller(deps, [], { intervalMs: 10 }).start();
    await new Promise((resolve) => setTimeout(resolve, 50));
    stop();

    expect(finalizeCalls(fetchMock)[0]?.envelope.type).toBe("chapter_analysis");
  });

  it("analyze-chapter projects the immutable inner schema version", async () => {
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "chapter_analysis",
        schema_version: "chapter-analysis.v1",
        analysis: {
          chapter_id: 1,
          chapter_number: 1,
          source_snapshot_hash: "a".repeat(64),
          input_hash: "b".repeat(64),
          cutoff: 10,
          max_length: 2000,
          spoiler_policy_version: "spoiler-policy.v1",
          chapter_digest: "c".repeat(64),
          chunk_digests: ["d".repeat(64)],
          previous_context_summary: null,
          next_context_hint: null,
          next_hint_reason_code: "hint_unavailable",
          continuity_notes: "第一章建立主角处境。",
        },
      }),
      toolResults: [
        {
          role: "toolResult",
          toolName: "get_chapter",
          toolCallId: "chapter-1",
          isError: false,
          content: [{ type: "text", text: "第一章正文" }],
        },
      ],
    });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "analyze-chapter" });

    const stop = createPoller(deps, [], { intervalMs: 10 }).start();
    await new Promise((resolve) => setTimeout(resolve, 50));
    stop();

    expect(finalizeCalls(fetchMock)[0]?.envelope.analysis.schema_version).toBe(
      "chapter-analysis-artifact.v1",
    );
    expect(finalizeCalls(fetchMock)[0]?.envelope.analysis).toEqual(
      expect.objectContaining({
        chapter_id: 1,
        chapter_number: 1,
        cutoff: 1,
        input_hash: "a".repeat(64),
        source_snapshot_hash: expect.stringMatching(/^[0-9a-f]{64}$/),
        chapter_digest: expect.stringMatching(/^[0-9a-f]{64}$/),
      }),
    );
    expect(finalizeCalls(fetchMock)[0]?.source_versions).toEqual(
      finalizeCalls(fetchMock)[0]?.envelope.source_versions,
    );
  });

  it("uses the production Skill loader when the poller dependency is omitted", async () => {
    const { deps } = makePollerDeps({
      lastText: JSON.stringify({
        type: "chapter_analysis",
        schema_version: "chapter-analysis.v1",
        analysis: {
          schema_version: "chapter-analysis-artifact.v1",
          chapter_id: 1,
          chapter_number: 1,
          source_snapshot_hash: "a".repeat(64),
          input_hash: "b".repeat(64),
          cutoff: 10,
          max_length: 2000,
          spoiler_policy_version: "spoiler-policy.v1",
          chapter_digest: "c".repeat(64),
          chunk_digests: ["d".repeat(64)],
          previous_context_summary: null,
          next_context_hint: null,
          next_hint_reason_code: null,
          continuity_notes: "第一章建立主角处境。",
        },
      }),
      toolResults: [
        {
          role: "toolResult",
          toolName: "get_chapter",
          toolCallId: "chapter-1",
          isError: false,
          content: [{ type: "text", text: "第一章正文" }],
        },
      ],
    });
    delete deps.loadSkillImpl;
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { skillName: "analyze-chapter" });

    const stop = createPoller(deps, [], { intervalMs: 10 }).start();
    await new Promise((resolve) => setTimeout(resolve, 50));
    stop();

    expect(finalizeCalls(fetchMock)).toEqual([
      expect.objectContaining({
        stop_reason: "stop",
        envelope: expect.objectContaining({ type: "chapter_analysis" }),
      }),
    ]);
    expect(cancelCalls(fetchMock)).toHaveLength(0);
  });

  it("trips the in-run budget breaker when tool calls exceed max_calls", async () => {
    const { deps, session } = makePollerDeps({ emitToolStarts: 5 });
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installBackendMock(fetchMock, { budgetSnapshot: { max_calls: 2 } });

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 80));
    stop();

    // 第 3 次工具调用触发熔断 → abort 被调用，run 以失败终态 finalize。
    expect(session.abort).toHaveBeenCalled();
    const finalizes = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith("/finalize") && (c[1] as RequestInit)?.method === "POST",
    );
    expect(finalizes.length).toBe(1);
    const body = JSON.parse(String(finalizes[0][1]?.body ?? "{}"));
    expect(body.stop_reason).not.toBe("stop");
  });
});


describe("guided 模式（build-visual-bible：确定性检索 + 单轮生成）", () => {
  const SPAN_HASH = "c".repeat(64);
  const SPAN_KEY = `qp:1:0:40:${SPAN_HASH}`;

  /** guided 路径的 fetch mock：queued → claim → gateway chat → finalize。 */
  function installGuidedMock(fetchMock: ReturnType<typeof vi.fn>, modelText: string) {
    let queued = true;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/queued-runs") && method === "GET") {
        const items = queued
          ? [{
              run_id: 1, owner_id: 2, novel_id: 6, skill_version_id: 9,
              input: { novel_id: 6, question: "死城的环境" },
              input_hash: "a".repeat(64), branch: null,
              backfill_dimension: "relations", origin: "chat_backfill",
              user_message_id: null,
            }]
          : [];
        queued = false;
        return Promise.resolve(new Response(JSON.stringify({ items, total: items.length }), { status: 200 }));
      }
      if (url.endsWith("/claim") && method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({
          run_id: 1, owner_id: 2, novel_id: 6, skill_version_id: 9,
          skill_name: "build-visual-bible",
          input: {
            novel_id: 6, question: "死城的环境",
            source_snapshot: { snapshot_hash: "b".repeat(64) },
            cutoff_chapter: 53,
          },
          input_hash: "a".repeat(64), branch: null,
          backfill_dimension: "relations", origin: "chat_backfill",
          user_message_id: null, frozen_manifest: {}, budget_snapshot: {},
          internal_token: "tok-guided",
        }), { status: 200 }));
      }
      if (url.includes("/api/gateway/v1/chat/completions")) {
        return Promise.resolve(new Response(JSON.stringify({
          choices: [{ message: { role: "assistant", content: modelText }, finish_reason: "stop" }],
          usage: { input: 10, output: 5 },
        }), { status: 200 }));
      }
      if (url.endsWith("/finalize") && method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({ artifact: { id: 7 } }), { status: 200 }));
      }
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
  }

  /** guided 检索 fake：一次 search + 一次 span 物化。 */
  const guidedCallTool = async (name: string, params: unknown) => {
    if (name === "search_novel_text") {
      return {
        content: [{ type: "text" as const, text: JSON.stringify({
          results: [{ chunk_id: 11, chapter_id: 1, chapter_number: 51, score: 0.9 }],
        }) }] as [{ type: "text"; text: string }],
      };
    }
    if (name === "get_evidence_span") {
      return {
        content: [{ type: "text" as const, text: JSON.stringify({
          evidence_key: SPAN_KEY, chapter_id: 1, chapter_number: 51,
          novel_id: 6, source_start: 0, source_end: 40,
          content_hash: SPAN_HASH, excerpt: "死城景象摘录",
        }) }] as [{ type: "text"; text: string }],
      };
    }
    throw new Error(`unexpected ${name}`);
  };

  function guidedFinalizeCalls(fetchMock: ReturnType<typeof vi.fn>) {
    return fetchMock.mock.calls
      .filter(
        (c) => String(c[0]).endsWith("/finalize") && (c[1] as RequestInit)?.method === "POST",
      )
      .map((c) => JSON.parse(String((c[1] as RequestInit)?.body ?? "{}")));
  }

  it("单轮生成 + 编号映射：finalize 信封携带真实 evidence_key", async () => {
    const modelText = JSON.stringify({
      visual_bible: {
        entities: [{
          entity_key: "place-dead-city", entity_type: "place",
          description: "死城", authority: "canon_fact",
        }],
        claims: [{
          entity_key: "place-dead-city", authority: "canon_fact",
          description: "死城布满眼球状凸起", evidence_indices: [1],
        }],
      },
    });
    const { deps } = makePollerDeps();
    deps.guidedCallTool = guidedCallTool;
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installGuidedMock(fetchMock, modelText);

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 80));
    stop();

    const finals = guidedFinalizeCalls(fetchMock);
    expect(finals.length).toBe(1);
    expect(finals[0].stop_reason).toBe("stop");
    const version = finals[0].envelope.visual_bible;
    expect(version.review_state).toBe("candidate");
    expect(version.claims[0].evidence_refs[0].evidence_key).toBe(SPAN_KEY);
    expect(version.claims[0].evidence_refs[0].content_hash).toBe(SPAN_HASH);
    expect(finals[0].envelope.evidence_refs).toEqual([SPAN_KEY]);
  });

  it("编号越界 → repair 一轮后仍越界 → failed 终态零写入", async () => {
    const modelText = JSON.stringify({
      visual_bible: {
        entities: [{
          entity_key: "place-dead-city", entity_type: "place",
          description: "死城", authority: "canon_fact",
        }],
        claims: [{
          entity_key: "place-dead-city", authority: "canon_fact",
          description: "编造编号", evidence_indices: [9],
        }],
      },
    });
    const { deps } = makePollerDeps();
    deps.guidedCallTool = guidedCallTool;
    const fetchMock = deps.fetchImpl as ReturnType<typeof vi.fn>;
    installGuidedMock(fetchMock, modelText);

    const poller = createPoller(deps, [], { intervalMs: 10 });
    const stop = poller.start();
    await new Promise((r) => setTimeout(r, 120));
    stop();

    const finals = guidedFinalizeCalls(fetchMock);
    expect(finals.length).toBe(1);
    expect(finals[0].stop_reason).toBe("error");
    expect(finals[0].envelope).toEqual({});
  });
});
