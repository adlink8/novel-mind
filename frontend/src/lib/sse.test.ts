/**
 * lib/sse.ts 单元测试（25.2-04 Task 1）。
 *
 * 用脚本化的假 fetch 返回假 Response（jsdom 无原生 fetch/Response）：
 * - 跨 chunk 边界帧：同一帧的 `data:` 被拆进两个 chunk，仍恰好派发一次且完整。
 * - abort：读流挂起直到 abort → reject `DOMException("Aborted", "AbortError")`。
 * - 非 2xx：401 → reject 携带 status 401，且不派发任何帧。
 * - malformed：畸形帧 → onError 一次，后续好帧仍正常派发。
 * - 方法/头：POST + application/json + Authorization Bearer。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { streamAgentRun, type AgentRunFrame } from "./sse";

const ENCODER = new TextEncoder();

/** 脚本化假 Response body：按 chunk 逐块吐出文本；abort 时拒绝。 */
function scriptedBody(
  chunks: string[],
  opts?: { signal?: AbortSignal; hangAfter?: number }
) {
  let i = 0;
  return {
    getReader() {
      return {
        async read(): Promise<{ done: boolean; value?: Uint8Array }> {
          if (i < chunks.length) {
            return { done: false, value: ENCODER.encode(chunks[i++]) };
          }
          // 脚本块耗尽后：若配置了 hang，挂起直到 abort（模拟长时间未结束的流）。
          if (opts?.hangAfter != null && i === opts.hangAfter) {
            await new Promise<never>((_, reject) => {
              opts.signal?.addEventListener(
                "abort",
                () => reject(new DOMException("Aborted", "AbortError")),
                { once: true }
              );
            });
          }
          return { done: true, value: undefined };
        },
      };
    },
  };
}

function scriptedFetch(
  status: number,
  body: unknown,
  ok = status >= 200 && status < 300
) {
  return vi.fn().mockResolvedValue({ ok, status, body });
}

describe("streamAgentRun", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("POSTs JSON with Bearer Authorization and content-type application/json", async () => {
    globalThis.fetch = scriptedFetch(200, scriptedBody([]));
    await streamAgentRun("/agent/novels/11/runs", { question: "q" }, {
      onEvent: vi.fn(),
    });
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
      .calls[0] as [string, RequestInit];
    expect(url).toBe("/agent/novels/11/runs");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      Authorization: "Bearer ",
    });
    expect(JSON.parse(String(init.body))).toEqual({ question: "q" });
  });

  it("splits frames across chunk boundaries and dispatches exactly once, complete", async () => {
    const frame = JSON.stringify({ type: "delta", text: "跨块帧内容" });
    const half = Math.floor(frame.length / 2);
    // 帧被拆成两半，且第一半甚至不包含 "data: " 前缀 —— 完全依赖跨 chunk 拼接。
    const body = scriptedBody([
      `data: ${frame.slice(0, half)}`,
      `${frame.slice(half)}\n\n`,
    ]);
    globalThis.fetch = scriptedFetch(200, body);

    const onEvent = vi.fn();
    await streamAgentRun("/agent/novels/11/runs", {}, { onEvent });

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith({
      type: "delta",
      text: "跨块帧内容",
    } satisfies AgentRunFrame);
  });

  it("dispatches multiple frames from one chunk and ignores non-data lines", async () => {
    const body = scriptedBody([
      `event: ping\n\n` +
        `data: ${JSON.stringify({ type: "tool_start", toolName: "search_novel_text", args: {} })}\n\n` +
        `data: ${JSON.stringify({ type: "run_end", runId: "7", status: "completed" })}\n\n`,
    ]);
    globalThis.fetch = scriptedFetch(200, body);

    const onEvent = vi.fn();
    await streamAgentRun("/agent/novels/11/runs", {}, { onEvent });

    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent).toHaveBeenNthCalledWith(1, {
      type: "tool_start",
      toolName: "search_novel_text",
      args: {},
    });
    expect(onEvent).toHaveBeenNthCalledWith(2, {
      type: "run_end",
      runId: "7",
      status: "completed",
    });
  });

  it("calls onError for a malformed frame and keeps parsing later frames", async () => {
    const body = scriptedBody([
      `data: {not-valid-json}\n\n` +
        `data: ${JSON.stringify({ type: "delta", text: "好帧" })}\n\n`,
    ]);
    globalThis.fetch = scriptedFetch(200, body);

    const onEvent = vi.fn();
    const onError = vi.fn();
    await streamAgentRun("/agent/novels/11/runs", {}, { onEvent, onError });

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith({ type: "delta", text: "好帧" });
  });

  it("rejects with AbortError when the stream is aborted", async () => {
    const ac = new AbortController();
    const body = scriptedBody(
      [`data: ${JSON.stringify({ type: "delta", text: "第一帧" })}\n\n`],
      { signal: ac.signal, hangAfter: 1 }
    );
    globalThis.fetch = scriptedFetch(200, body);

    const onEvent = vi.fn();
    const promise = streamAgentRun(
      "/agent/novels/11/runs",
      {},
      { signal: ac.signal, onEvent }
    );

    // 等第一帧派发后 abort（此时读流正挂在第二块上）。
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledTimes(1));
    ac.abort();

    await expect(promise).rejects.toMatchObject({
      name: "AbortError",
      message: "Aborted",
    });
  });

  it("rejects with the HTTP status on a 401 response and never dispatches frames", async () => {
    globalThis.fetch = scriptedFetch(401, null);

    const onEvent = vi.fn();
    const promise = streamAgentRun("/agent/novels/11/runs", {}, { onEvent });

    await expect(promise).rejects.toMatchObject({ status: 401 });
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("rejects when the response has no readable body", async () => {
    globalThis.fetch = scriptedFetch(200, null);

    await expect(
      streamAgentRun("/agent/novels/11/runs", {}, { onEvent: vi.fn() })
    ).rejects.toThrow(/no readable body/);
  });
});
