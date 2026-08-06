import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { createApp } from "../src/server.js";
import { SkillValidationError } from "../src/skills/loader.js";
import { loadSkill } from "../src/skills/loader.js";

/**
 * server.test.ts（25.2-05 Task 4，run-API 契约）：
 * - 401（无 Authorization，禁匿名 run）
 * - happy-path 帧顺序：delta / tool_start / tool_end / turn_end →
 *   artifact preview → 恰一个 run_end（completed）
 * - agent_end stop → finalize POST 发生在 run_end 之前
 * - 客户端断开 → session.abort() + 后端 cancel POST（disconnect-cancel，D-19）
 * - input 校验失败 → 422 且零会话创建
 * 全部用 mock fetch + mock Pi 会话；不真连 backend。
 */

/** 读取完整 SSE 响应并解析为帧数组。 */
async function collectSse(res: Response): Promise<unknown[]> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const frames: unknown[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) {
          frames.push(JSON.parse(line.slice("data: ".length)));
        }
      }
    }
  }
  return frames;
}

/** 可编程 fake 会话：可注入事件脚本、可控 prompt 挂起、记录 abort。 */
function fakeSession(initialMessages: unknown[] = []) {
  let listener: ((ev: unknown) => void) | undefined;
  let promptResolve: (() => void) | undefined;
  let promptReject: ((err: Error) => void) | undefined;
  return {
    sessionId: "session-1",
    messages: [...initialMessages],
    subscribe: vi.fn((l: (ev: unknown) => void) => {
      listener = l;
      return () => {
        listener = undefined;
      };
    }),
    prompt: vi.fn(
      () =>
        new Promise<void>((resolve, reject) => {
          promptResolve = resolve;
          promptReject = reject;
        }),
    ),
    abort: vi.fn(() => {
      promptResolve?.();
    }),
    isIdle: false,
    /** 测试驱动：发射一个 Pi 事件。 */
    emit(ev: unknown): void {
      listener?.(ev);
    },
    /** 测试驱动：prompt resolve 前追加消息（模拟 agent_end 的 stopReason）。 */
    finish(messages: unknown[]): void {
      this.messages.push(...messages);
      promptResolve?.();
    },
    /** 测试驱动：prompt reject（模拟运行异常）。 */
    fail(err: Error): void {
      promptReject?.(err);
    },
  };
}

type FakeSession = ReturnType<typeof fakeSession>;

describe("agent-service run API (POST /agent/novels/{novel_id}/runs)", () => {
  let server: ReturnType<typeof createApp>;
  let port: number;
  let fetchMock: ReturnType<typeof vi.fn>;
  let createSessionMock: ReturnType<typeof vi.fn>;
  const baseUrl = "http://127.0.0.1:8000";
  const AUTH = "Bearer end-user-jwt";

  /** 默认 backend mock：versions → run-create 202 → cancel/finalize 200。 */
  function installDefaultBackendMock() {
    fetchMock.mockClear(); // 清调用记录，避免跨测试累积干扰计数断言
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.endsWith("/versions") && init?.method === "GET") {
        return Promise.resolve(
          new Response(JSON.stringify({ items: [{ id: 5, status: "active" }], total: 1 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url.endsWith("/skill-runs") && init?.method === "POST") {
        return Promise.resolve(
          new Response(JSON.stringify({ run: { id: 42 }, internal_token: "tok-42" }), {
            status: 202,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url.endsWith("/cancel") && init?.method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
      }
      if (url.endsWith("/finalize") && init?.method === "POST") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              artifact: { id: 7, type: "cited_answer", status: "candidate" },
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(new Response(JSON.stringify({ error: { code: "upstream_error" } }), { status: 502 }));
    });
  }

  function startServer(session: FakeSession) {
    // 每次启动都重置 mock，避免跨测试累积调用/返回值泄漏。
    createSessionMock.mockReset();
    createSessionMock.mockResolvedValue(session);
    server = createApp({
      fetchImpl: fetchMock as unknown as typeof fetch,
      createSessionImpl: createSessionMock as unknown as (opts: unknown) => Promise<never>,
    });
    return new Promise<void>((resolve) => {
      server.listen(0, () => {
        const addr = server.address();
        port = typeof addr === "object" && addr ? addr.port : 0;
        resolve();
      });
    });
  }

  beforeAll(() => {
    fetchMock = vi.fn();
    createSessionMock = vi.fn();
  });

  afterAll(() => {
    server?.close();
  });

  it("401：无 Authorization 头，禁匿名 run", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question: "阿宁在竹林里看见了谁？" }),
    });
    expect(res.status).toBe(401);
    expect(createSessionMock).not.toHaveBeenCalled();
    await res.body?.cancel();
  });

  it("happy-path：精选帧有序、artifact preview 后恰一个 run_end(completed)，且 finalize 先于 run_end", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "阿宁在竹林里看见了谁？" }),
    });
    expect(res.headers.get("content-type")).toContain("text/event-stream");

    // 发射精选事件脚本
    session.emit({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "竹" } });
    session.emit({ type: "message_update", assistantMessageEvent: { type: "start" } }); // 无 delta → 丢弃
    session.emit({ type: "tool_execution_start", toolName: "get_chapter", args: { novel_id: 1, chapter_id: 2 } });
    session.emit({ type: "tool_execution_end", toolName: "get_chapter", isError: false });
    session.emit({ type: "turn_end", message: { usage: { input: 3, output: 4 } } });
    session.emit({ type: "agent_start" }); // 非精选 → 丢弃

    // agent_end：stopReason=stop，finalize 触发
    session.finish([
      {
        role: "assistant",
        stopReason: "stop",
        provider: "novelmind",
        model: "reader-chat-default",
        usage: { input: 5, output: 6 },
        content: [{ type: "text", text: "答案：阿宁看见了小狐狸。" }],
      },
    ]);

    const frames = await collectSse(res);
    const types = frames.map((f) => (f as { type: string }).type);

    expect(types).toEqual([
      "delta",
      "tool_start",
      "tool_end",
      "turn_end",
      "artifact",
      "run_end",
    ]);
    // 恰一个 run_end，且状态 completed
    const runEnds = frames.filter((f) => (f as { type: string }).type === "run_end");
    expect(runEnds).toHaveLength(1);
    expect(runEnds[0]).toMatchObject({ type: "run_end", runId: "42", status: "completed" });
    // finalize POST 先于 run_end 帧发生
    const finalizeCalls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith("/finalize") && (c[1] as RequestInit)?.method === "POST",
    );
    expect(finalizeCalls).toHaveLength(1);
    // artifact preview 来自 finalize 响应
    expect(frames[4]).toMatchObject({ type: "artifact", artifact: { id: 7, status: "candidate" } });
  });

  it("agent_end stopReason=aborted → run_end(cancelled)，cancel POST 触发，无 finalize", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "阿宁在竹林里看见了谁？" }),
    });
    session.emit({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "部" } });
    session.finish([{ role: "assistant", stopReason: "aborted", usage: {}, content: [] }]);

    const frames = await collectSse(res);
    const runEnd = frames.find((f) => (f as { type: string }).type === "run_end");
    expect(runEnd).toMatchObject({ type: "run_end", runId: "42", status: "cancelled" });
    const cancelCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/cancel"));
    expect(cancelCalls.length).toBeGreaterThan(0);
    const finalizeCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/finalize"));
    expect(finalizeCalls).toHaveLength(0);
  });

  it("agent_end stopReason=error → finalize POST(stop_reason=error) 先于 run_end(failed)，且无 cancel（落库防 run 永久卡 queued）", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "阿宁在竹林里看见了谁？" }),
    });
    session.emit({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "错" } });
    // agent_end：stopReason=error（上游异常）→ 必须落库 finalize（failed + 0 artifact），
    // 而非仅发帧——否则后端 run 全程 status='queued' 永不迁移。
    session.finish([
      {
        role: "assistant",
        stopReason: "error",
        provider: "novelmind",
        model: "reader-chat-default",
        usage: { input: 5, output: 6 },
        content: [],
      },
    ]);

    const frames = await collectSse(res);
    const runEnd = frames.find((f) => (f as { type: string }).type === "run_end");
    expect(runEnd).toMatchObject({ type: "run_end", runId: "42", status: "failed", error_code: "upstream_error" });

    // error → finalize POST 且 stop_reason='error'；不用 cancel（queued→cancelled 语义错误）
    const finalizeCalls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith("/finalize") && (c[1] as RequestInit)?.method === "POST",
    );
    expect(finalizeCalls).toHaveLength(1);
    const finalizeBody = JSON.parse((finalizeCalls[0][1] as RequestInit).body as string) as Record<string, unknown>;
    expect(finalizeBody.stop_reason).toBe("error");
    expect(finalizeBody.envelope).toEqual({});
    // model_lineage 从 last assistant 消息透传
    expect(finalizeBody.model_lineage).toEqual({ provider: "novelmind", model: "reader-chat-default" });

    const cancelCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/cancel"));
    expect(cancelCalls).toHaveLength(0);
  });

  it("客户端断开（fetch abort）→ session.abort() + 后端 cancel POST（disconnect-cancel）", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    const ctrl = new AbortController();
    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "阿宁在竹林里看见了谁？" }),
      signal: ctrl.signal,
    });
    expect(res.status).toBe(200);
    session.emit({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "断" } });
    // prompt 挂起中；客户端中断连接（fetch abort → server req close）
    ctrl.abort();

    // 等待 server 端 onClose 完成 abort + cancel
    await vi.waitFor(
      () => {
        expect(session.abort).toHaveBeenCalled();
      },
      { timeout: 5000 },
    );
    await vi.waitFor(
      () => {
        const cancelCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/cancel"));
        expect(cancelCalls.length).toBeGreaterThan(0);
      },
      { timeout: 5000 },
    );
    // cancel-no-write：断开不会触发 finalize
    const finalizeCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/finalize"));
    expect(finalizeCalls).toHaveLength(0);
  });

  it("input 校验失败 → 422 且零会话创建", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    // 覆盖 validateRunInput 抛错：不合法输入在任何会话/工具调用前拦截
    createSessionMock.mockReset(); // input 校验失败时不应有任何会话创建
    const app2 = createApp({
      fetchImpl: fetchMock as unknown as typeof fetch,
      createSessionImpl: createSessionMock as unknown as (opts: unknown) => Promise<never>,
      validateRunInputImpl: () => {
        throw new SkillValidationError("answer-reading-question", "input", ["question required"]);
      },
    });
    await new Promise<void>((resolve) => app2.listen(0, () => resolve()));
    const addr2 = app2.address();
    const port2 = typeof addr2 === "object" && addr2 ? addr2.port : 0;

    const res = await fetch(`http://127.0.0.1:${port2}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "" }),
    });
    expect(res.status).toBe(422);
    expect(createSessionMock).not.toHaveBeenCalled();
    app2.close();
  });

  it("未知技能 → 404（fail-closed loader 语义透传）", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "q", skill: "nonexistent-skill" }),
    });
    expect(res.status).toBe(404);
    expect(createSessionMock).not.toHaveBeenCalled();
    await res.body?.cancel();
  });

  it("run 分发只走一次 run-create（202 是唯一授权；工具 auth 用 per-run 内部令牌）", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    createSessionMock.mockImplementation((opts: { auth: string }) => {
      expect(opts.auth).toBe("Bearer tok-42"); // per-run 内部令牌而非端用户 JWT
      return Promise.resolve(session);
    });
    await startServer(session);

    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "阿宁在竹林里看见了谁？" }),
    });
    session.emit({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "终" } });
    session.finish([{ role: "assistant", stopReason: "stop", provider: "novelmind", model: "reader-chat-default", usage: {}, content: [{ type: "text", text: "终答" }] }]);
    await collectSse(res);

    const runCreateCalls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).endsWith("/skill-runs") && (c[1] as RequestInit)?.method === "POST",
    );
    expect(runCreateCalls).toHaveLength(1);
  });

  it("真实 skill 资产可通过 loader 默认路径加载（server 集成基线）", () => {
    const skill = loadSkill("answer-reading-question");
    expect(skill.allowedTools).toContain("get_chapter");
    expect(skill.allowedTools).not.toContain("get_narrative_memory");
  });

  it("P1: 分析/生图 skill 经 SSE 不再被 question 注入 422（input 原样校验）", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);
    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/6/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({
        question: "为第1章生成插图",
        skill: "illustrate-scene",
        input: {
          prompt_revision_id: 1,
          visual_bible_version_id: 1,
          scene_spec_revision_id: 1,
          source_snapshot_id: "ss-1",
          job_key: "p1-test",
        },
      }),
    });
    // 不再 422（question 不再注入 → schema additionalProperties:false 通过）。
    expect(res.status).toBe(200);
    // prompt 收到非空占位（question 缺省时用 skill 名兜底）。
    expect(session.prompt).toHaveBeenCalled();
    const promptCalls = session.prompt.mock.calls as unknown[];
    expect(promptCalls.length).toBeGreaterThan(0);
    const firstArg = (promptCalls[0] as [unknown])[0];
    expect(String(firstArg ?? "").length).toBeGreaterThan(0);
  });

  it("body.skill 缺省 → 服务端按意图自动路由（route-skill 返回的主 skill 生效，Agent 选 skill）", async () => {
    fetchMock.mockClear();
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/route-skill") && init?.method === "POST") {
        return Promise.resolve(
          new Response(
            JSON.stringify({ skills: ["detect-key-scenes"], primary: "detect-key-scenes" }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }
      if (String(url).endsWith("/versions") && init?.method === "GET") {
        return Promise.resolve(
          new Response(JSON.stringify({ items: [{ id: 9, status: "active" }], total: 1 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (String(url).endsWith("/skill-runs") && init?.method === "POST") {
        return Promise.resolve(
          new Response(JSON.stringify({ run: { id: 43 }, internal_token: "tok-43" }), {
            status: 202,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (String(url).endsWith("/finalize") && init?.method === "POST") {
        return Promise.resolve(
          new Response(JSON.stringify({ artifact: { id: 8 } }), { status: 200 }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: "upstream_error" } }), { status: 502 }),
      );
    });

    const session = fakeSession();
    createSessionMock.mockReset();
    createSessionMock.mockResolvedValue(session);
    server = createApp({
      fetchImpl: fetchMock as unknown as typeof fetch,
      createSessionImpl: createSessionMock as unknown as (opts: unknown) => Promise<never>,
    });
    await new Promise<void>((resolve) => server.listen(0, () => resolve()));
    const addr = server.address();
    const routePort = typeof addr === "object" && addr ? addr.port : 0;

    const res = await fetch(`http://127.0.0.1:${routePort}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "第1章有哪些关键场景" }),
    });
    expect(res.status).toBe(200);

    const createOpts = createSessionMock.mock.calls[0]?.[0] as { skill?: { name?: string } };
    expect(createOpts.skill?.name).toBe("detect-key-scenes");
    // 技能版本查询指向自动路由的 skill。
    const versionCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/versions"));
    expect(String(versionCalls[0][0])).toContain("detect-key-scenes");
    await res.body?.cancel();
  });

  it("route-skill 不可用/失败 → 保守回退 answer-reading-question（默认行为保留）", async () => {
    // 默认 mock 对 route-skill 返回 502 → 回退问答 skill。
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "阿宁在竹林里看见了谁？" }),
    });
    expect(res.status).toBe(200);
    const createOpts = createSessionMock.mock.calls[0]?.[0] as { skill?: { name?: string } };
    expect(createOpts.skill?.name).toBe("answer-reading-question");
    const versionCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/versions"));
    expect(String(versionCalls[0][0])).toContain("answer-reading-question");
    await res.body?.cancel();
  });

  it("显式 body.skill 覆盖自动路由（不调用 route-skill）", async () => {
    installDefaultBackendMock();
    const session = fakeSession();
    await startServer(session);

    const res = await fetch(`http://127.0.0.1:${port}/agent/novels/1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: AUTH },
      body: JSON.stringify({ question: "q", skill: "answer-reading-question" }),
    });
    expect(res.status).toBe(200);
    const routeCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/route-skill"));
    expect(routeCalls).toHaveLength(0);
    await res.body?.cancel();
  });
});
