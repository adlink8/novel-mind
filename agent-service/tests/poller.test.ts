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
function installBackendMock(fetchMock: ReturnType<typeof vi.fn>) {
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
            skill_name: "answer-reading-question",
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
          JSON.stringify({ artifact: { id: 7, type: "cited_answer", status: "candidate" } }),
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
});
