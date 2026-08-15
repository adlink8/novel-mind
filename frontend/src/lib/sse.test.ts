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

import {
  streamAgentRun,
  runAgentStream,
  isRunTerminal,
  type AgentRunFrame,
} from "./sse";
import { endpointResolver } from "./runtime/endpoint-resolver";
import type { DesktopBridge } from "../../../desktop/src/shared/bridge-contract";

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

// ---------------------------------------------------------------------------
// runAgentStream (44-03): authoritative terminals, single reconnect, replay
// dedupe and session rotation (T-44-03-01/02, D-44-05).
// ---------------------------------------------------------------------------

function runFrame(overrides: Partial<AgentRunFrame> = {}): AgentRunFrame {
  return { type: "run_end", runId: "7", status: "completed", ...overrides };
}

/** Installs a scripted fetch that serves a sequence of SSE bodies. */
function scriptedStream(
  bodies: Array<{ status: number; chunks: string[]; hangAfter?: number }>,
): ReturnType<typeof vi.fn> {
  let i = 0;
  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    const spec = bodies[Math.min(i, bodies.length - 1)];
    if (spec === undefined) throw new Error("no scripted response");
    i += 1;
    if (spec.status !== 200) {
      return { ok: false, status: spec.status, body: null };
    }
    return { ok: true, status: 200, body: scriptedBody(spec.chunks, { signal: init?.signal ?? undefined, hangAfter: spec.hangAfter }) };
  });
  globalThis.fetch = fetchMock;
  return fetchMock;
}

/** Body that emits the given chunks then rejects with a network TypeError
 * (simulates a mid-stream drop after frames were delivered). */
function dropBody(chunks: string[]): unknown {
  let i = 0;
  return {
    getReader() {
      return {
        async read(): Promise<{ done: boolean; value?: Uint8Array }> {
          if (i < chunks.length) {
            return { done: false, value: ENCODER.encode(chunks[i++]) };
          }
          throw new TypeError("network dropped mid-stream");
        },
      };
    },
  };
}

function browserMode(): void {
  delete (window as unknown as Record<string, unknown>)["novelMindDesktop"];
  endpointResolver.invalidate();
}

function withAgentDesktopBridge(): void {
  const bridge: DesktopBridge = {
    getRuntimeStatus: async () => ({
      ready: true,
      appVersion: "0.1.0",
      electronVersion: "43.3.0",
      security: { sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true },
    }),
    requestRuntimeRestart: async () => ({ ok: true }),
    getBootstrap: async () => ({
      appVersion: "0.1.0",
      bridgeVersion: 1,
      features: ["desktop-shell"],
      runtime: { status: "ready", session: makeSession() },
      credentials: {
        provider: "unavailable",
        localAuth: "unavailable",
        storageAvailable: false,
      },
    }),
    openExternalLink: async (url: string) =>
      url.startsWith("https://") ? { ok: true } : { ok: false, code: "REJECTED", reason: "not https" },
    onRuntimeStatus: () => ({ unsubscribe: () => {} }),
    getLocalAuthToken: async () => "sess-token",
  };
  (window as unknown as Record<string, unknown>)["novelMindDesktop"] = bridge;
  endpointResolver.invalidate();
}

/** A ready bootstrap session (dynamic loopback ports). */
function makeSession() {
  const components = {
    next: { host: "127.0.0.1" as const, port: 41003 },
    fastapi: { host: "127.0.0.1" as const, port: 41001 },
    agent_service: { host: "127.0.0.1" as const, port: 41002 },
    postgres_pgvector: { host: "127.0.0.1" as const, port: 41000 },
    vector_store: { host: "127.0.0.1" as const, port: 41004 },
  };
  return {
    sessionId: "sess-abc",
    issuedAt: "2026-08-10T00:00:00.000Z",
    expiresAt: "2026-08-10T01:00:00.000Z",
    components,
    services: { api: components.fastapi, agent: components.agent_service, renderer: components.next },
    capabilities: { agentStreaming: true },
  };
}

describe("runAgentStream (44-03)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    browserMode();
  });

  afterEach(() => {
    browserMode();
  });

  it("resolves with the authoritative terminal status from the server", async () => {
    scriptedStream([
      { status: 200, chunks: [`data: ${JSON.stringify(runFrame({ status: "completed" }))}\n\n`] },
    ]);
    const onEvent = vi.fn();
    const terminal = await runAgentStream({
      url: "/agent/novels/11/runs",
      body: { question: "q" },
      onEvent,
    });
    expect(terminal).toBe("completed");
    expect(onEvent).toHaveBeenCalledWith(runFrame({ status: "completed" }));
  });

  it("keeps cancelled cancelled and failed failed across a mid-stream drop", async () => {
    // First connection emits a delta then drops (network failure, no terminal);
    // the single reconnect serves the authoritative cancelled terminal.
    let i = 0;
    const fetchMock = vi.fn().mockImplementation(async () => {
      i += 1;
      if (i === 1) {
        return {
          ok: true,
          status: 200,
          body: dropBody([`data: ${JSON.stringify({ type: "delta", text: "hi" })}\n\n`]),
        };
      }
      return {
        ok: true,
        status: 200,
        body: scriptedBody([`data: ${JSON.stringify(runFrame({ status: "cancelled" }))}\n\n`]),
      };
    });
    globalThis.fetch = fetchMock;

    const onEvent = vi.fn();
    const terminal = await runAgentStream({
      url: "/agent/novels/11/runs",
      body: {},
      signal: new AbortController().signal,
      onEvent,
    });
    expect(terminal).toBe("cancelled");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not reconnect before any event frame (connection failure stays failed)", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network down"))
      .mockRejectedValueOnce(new TypeError("network down"));
    globalThis.fetch = fetchMock;
    await expect(
      runAgentStream({ url: "/agent/novels/11/runs", body: {}, onEvent: vi.fn() }),
    ).rejects.toThrow(/network down/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not reconnect on a non-2xx response (401 stays failed)", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 401, body: null });
    globalThis.fetch = fetchMock;
    await expect(
      runAgentStream({ url: "/agent/novels/11/runs", body: {}, onEvent: vi.fn() }),
    ).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("propagates external cancellation as AbortError", async () => {
    const ac = new AbortController();
    scriptedStream([
      {
        status: 200,
        chunks: [`data: ${JSON.stringify({ type: "delta", text: "x" })}\n\n`],
        hangAfter: 1,
      },
    ]);
    const onEvent = vi.fn();
    const promise = runAgentStream({
      url: "/agent/novels/11/runs",
      body: {},
      signal: ac.signal,
      onEvent,
    });
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledTimes(1));
    ac.abort();
    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects with code session-rotated when the runtime session changes mid-stream", async () => {
    withAgentDesktopBridge();
    // Gate the first connection's failure so the test can rotate the session
    // before the reconnect resolves.
    let releaseDrop: () => void = () => {};
    const dropped = new Promise<void>((resolve) => {
      releaseDrop = () => resolve();
    });
    let i = 0;
    const fetchMock = vi.fn().mockImplementation(async () => {
      i += 1;
      if (i === 1) {
        // First connection: emit one delta frame, then block until the test
        // releases the drop (session rotation happens in that window).
        let reads = 0;
        return {
          ok: true,
          status: 200,
          body: {
            getReader() {
              return {
                async read(): Promise<{ done: boolean; value?: Uint8Array }> {
                  reads += 1;
                  if (reads === 1) {
                    return {
                      done: false,
                      value: ENCODER.encode(
                        `data: ${JSON.stringify({ type: "delta", text: "x" })}\n\n`,
                      ),
                    };
                  }
                  await dropped;
                  throw new TypeError("network dropped mid-stream");
                },
              };
            },
          },
        };
      }
      return {
        ok: true,
        status: 200,
        body: scriptedBody([`data: ${JSON.stringify(runFrame({ status: "failed" }))}\n\n`]),
      };
    });
    globalThis.fetch = fetchMock;

    const onEvent = vi.fn();
    const promise = runAgentStream({
      url: "/agent/novels/11/runs",
      body: {},
      signal: new AbortController().signal,
      onEvent,
    });
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledTimes(1));
    // Runtime restart rotates the session before the drop is released.
    const bridge = (window as unknown as Record<string, unknown>)[
      "novelMindDesktop"
    ] as unknown as DesktopBridge;
    const rotated = makeSession();
    rotated.sessionId = "sess-rotated";
    (bridge.getBootstrap as unknown) = async () => ({
      appVersion: "0.1.0",
      bridgeVersion: 1,
      features: ["desktop-shell"],
      runtime: { status: "ready", session: rotated },
      credentials: {
        provider: "unavailable",
        localAuth: "unavailable",
        storageAvailable: false,
      },
    });
    releaseDrop();
    await expect(promise).rejects.toMatchObject({ code: "session-rotated" });
  });

  it("drops replay frames with a duplicate event_id (no second materialization)", async () => {
    const delta = {
      type: "delta",
      text: "a",
      event_id: "evt-1",
    };
    scriptedStream([
      {
        status: 200,
        chunks: [
          `data: ${JSON.stringify(delta)}\n\n`,
          `data: ${JSON.stringify({ ...delta, text: "b" })}\n\n`,
          `data: ${JSON.stringify(runFrame({ status: "completed" }))}\n\n`,
        ],
      },
    ]);
    const onEvent = vi.fn();
    const terminal = await runAgentStream({
      url: "/agent/novels/11/runs",
      body: {},
      eventId: "run-1",
      onEvent,
    });
    expect(terminal).toBe("completed");
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent.mock.calls[0]?.[0]).toMatchObject({ text: "a" });
  });

  it("treats a silent stream end without a terminal as failed (never success)", async () => {
    scriptedStream([{ status: 200, chunks: [`data: ${JSON.stringify({ type: "delta", text: "x" })}\n\n`] }]);
    const terminal = await runAgentStream({
      url: "/agent/novels/11/runs",
      body: {},
      onEvent: vi.fn(),
    });
    expect(terminal).toBe("failed");
  });

  it("recognizes the three authoritative terminal frames", () => {
    expect(isRunTerminal(runFrame({ status: "completed" }))).toBe(true);
    expect(isRunTerminal(runFrame({ status: "cancelled" }))).toBe(true);
    expect(isRunTerminal(runFrame({ status: "failed" }))).toBe(true);
    expect(isRunTerminal({ type: "delta", text: "x" })).toBe(false);
    expect(
      isRunTerminal({ type: "run_end", runId: "7", status: "something-else" }),
    ).toBe(false);
  });
});
