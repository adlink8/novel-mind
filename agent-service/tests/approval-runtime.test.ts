/**
 * approval-runtime.test.ts（25.3-06 / D-11 / REQ-AGENT-07）。
 *
 * server.ts 的 ask round trip（waitForApproval 短轮询 + gateAction 前置门控）单测：
 * - 轮询 verdict：approved / approved_for_session / rejected/expired/cancelled → denied
 * - run 取消（AbortSignal）停止轮询并解析 denied（T-25.3-04-02）
 * - gateAction 三态：allow 直接放行 / deny 抛 PolicyDenied（无审批路径）/
 *   ask POST + SSE 帧 + 轮询；approved_for_session 写入本 run SessionApprovals
 * - 决策权威只在 FastAPI：本函数不做任何本地决策
 */

import { describe, it, expect, vi } from "vitest";
import { gateAction, waitForApproval, type ApprovalVerdict } from "../src/server.js";
import { PolicyDenied } from "../src/policy/engine.js";
import { SessionApprovals } from "../src/policy/session-approvals.js";

const BASE = "http://backend.test";

type FetchMock = typeof fetch & {
  mock: { calls: Array<[string, RequestInit | undefined]> };
};

function makeFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>): FetchMock {
  const fn = vi.fn(
    (url: string, init?: RequestInit) => Promise.resolve(handler(url, init)),
  );
  return fn as unknown as FetchMock;
}

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** 收集 gateAction 发到 stream 的帧。 */
function makeStream() {
  const frames: unknown[] = [];
  return { frames, stream: { send: (frame: unknown) => frames.push(frame) } };
}

// ────────────────────────── waitForApproval 轮询 ──────────────────────────

describe("waitForApproval", () => {
  it("approved -> approved", async () => {
    const fetchImpl = makeFetch(() => jsonResponse({ status: "approved" }));
    const verdict = await waitForApproval({ fetchImpl, baseUrl: BASE, requestId: 1, authHeader: "Bearer x" });
    expect(verdict).toBe("approved");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("approved_for_session -> approved_for_session", async () => {
    const fetchImpl = makeFetch(() => jsonResponse({ status: "approved_for_session" }));
    const verdict = await waitForApproval({ fetchImpl, baseUrl: BASE, requestId: 1, authHeader: "Bearer x" });
    expect(verdict).toBe("approved_for_session");
  });

  it("rejected/expired/cancelled -> denied（终态即停）", async () => {
    for (const terminal of ["rejected", "expired", "cancelled"]) {
      const fetchImpl = makeFetch(() => jsonResponse({ status: terminal }));
      const verdict = await waitForApproval({ fetchImpl, baseUrl: BASE, requestId: 1, authHeader: "Bearer x" });
      expect(verdict).toBe("denied");
    }
  });

  it("pending 持续轮询直到非 pending", async () => {
    const states = ["pending", "pending", "approved"];
    const fetchImpl = makeFetch(() => jsonResponse({ status: states.shift() ?? "approved" }));
    const verdict = await waitForApproval({
      fetchImpl,
      baseUrl: BASE,
      requestId: 1,
      authHeader: "Bearer x",
      intervalMs: 5,
    });
    expect(verdict).toBe("approved");
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("run 取消（AbortSignal）停止轮询并解析 denied（cancellation stops polling）", async () => {
    const ac = new AbortController();
    const fetchImpl = makeFetch(() => jsonResponse({ status: "pending" }));
    const promise = waitForApproval({
      fetchImpl,
      baseUrl: BASE,
      requestId: 1,
      authHeader: "Bearer x",
      signal: ac.signal,
      intervalMs: 10,
    });
    // 立即取消：首轮 pending 之后或 sleep 期间 abort → denied。
    setTimeout(() => ac.abort(), 5);
    const verdict = await promise;
    expect(verdict).toBe("denied");
  });

  it("网络错误不终止轮询，恢复后仍可得 approved", async () => {
    let call = 0;
    const fetchImpl = makeFetch(() => {
      call += 1;
      if (call === 1) throw new Error("network down");
      return jsonResponse({ status: "approved" });
    });
    const verdict = await waitForApproval({
      fetchImpl,
      baseUrl: BASE,
      requestId: 1,
      authHeader: "Bearer x",
      intervalMs: 5,
    });
    expect(verdict).toBe("approved");
    expect(call).toBe(2);
  });
});

// ────────────────────────── gateAction 三态 ──────────────────────────

describe("gateAction", () => {
  const allowSkillRules = [{ action: "search_original_text", policy: "allow" as const }];

  it("allow：直接放行，零 fetch、零 SSE 帧", async () => {
    const fetchImpl = makeFetch(() => jsonResponse({}));
    const { frames, stream } = makeStream();
    const sa = new SessionApprovals();
    await gateAction({
      action: "search_original_text",
      skillRules: allowSkillRules,
      sessionApprovals: sa,
      runId: "7",
      novelId: 11,
      authHeader: "Bearer x",
      baseUrl: BASE,
      fetchImpl,
      stream,
    });
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(frames).toEqual([]);
  });

  it("deny：抛 PolicyDenied，无审批路径、零 POST", async () => {
    const fetchImpl = makeFetch(() => jsonResponse({}));
    const { frames, stream } = makeStream();
    const sa = new SessionApprovals();
    await expect(
      gateAction({
        action: "modify_original_canon",
        skillRules: [],
        sessionApprovals: sa,
        runId: "7",
        novelId: 11,
        authHeader: "Bearer x",
        baseUrl: BASE,
        fetchImpl,
        stream,
      }),
    ).rejects.toBeInstanceOf(PolicyDenied);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(frames).toEqual([]);
  });

  it("ask + approved：POST 审批请求、发 approval_request SSE 帧、verdict approved 放行", async () => {
    const fetchImpl = makeFetch((url, init) => {
      if (url.endsWith("/approval-requests") && init?.method === "POST") {
        return jsonResponse({ id: 42, action: "publish_illustration" }, true, 201);
      }
      return jsonResponse({ status: "approved" });
    });
    const { frames, stream } = makeStream();
    const sa = new SessionApprovals();
    await gateAction({
      action: "publish_illustration",
      skillRules: [],
      sessionApprovals: sa,
      runId: "7",
      novelId: 11,
      authHeader: "Bearer x",
      baseUrl: BASE,
      fetchImpl,
      stream,
      intervalMs: 5,
    });
    // POST 了审批请求 + 短轮询 GET。
    expect(fetchImpl.mock.calls.length).toBe(2);
    const postCall = fetchImpl.mock.calls[0];
    expect(postCall[1]?.method).toBe("POST");
    expect(JSON.parse(String(postCall[1]?.body)).run_id).toBe(7);
    expect(JSON.parse(String(postCall[1]?.body)).action).toBe("publish_illustration");
    // SSE 只通知：帧含 request 但不携带决策权威。
    expect(frames).toHaveLength(1);
    expect((frames[0] as { type: string }).type).toBe("approval_request");
    expect((frames[0] as { request: { id: number } }).request.id).toBe(42);
    // approved 不写入会话批准。
    expect(sa.size).toBe(0);
  });

  it("ask + approved_for_session：写入本 run SessionApprovals（D-11）", async () => {
    const fetchImpl = makeFetch((url, init) => {
      if (init?.method === "POST") return jsonResponse({ id: 9 }, true, 201);
      return jsonResponse({ status: "approved_for_session" });
    });
    const { stream } = makeStream();
    const sa = new SessionApprovals();
    await gateAction({
      action: "publish_illustration",
      skillRules: [],
      sessionApprovals: sa,
      runId: "7",
      novelId: 11,
      authHeader: "Bearer x",
      baseUrl: BASE,
      fetchImpl,
      stream,
      intervalMs: 5,
    });
    expect(sa.has("publish_illustration")).toBe(true);
    expect(sa.size).toBe(1);
  });

  it("ask + rejected/expired/cancelled/abort：抛 PolicyDenied", async () => {
    for (const terminal of ["rejected", "expired", "cancelled"]) {
      const fetchImpl = makeFetch((url, init) => {
        if (init?.method === "POST") return jsonResponse({ id: 3 }, true, 201);
        return jsonResponse({ status: terminal });
      });
      const { stream } = makeStream();
      await expect(
        gateAction({
          action: "publish_illustration",
          skillRules: [],
          sessionApprovals: new SessionApprovals(),
          runId: "7",
          novelId: 11,
          authHeader: "Bearer x",
          baseUrl: BASE,
          fetchImpl,
          stream,
          intervalMs: 5,
        }),
      ).rejects.toBeInstanceOf(PolicyDenied);
    }
  });

  it("run abort 取消审批轮询 -> PolicyDenied（cancellation stops polling）", async () => {
    const ac = new AbortController();
    const fetchImpl = makeFetch((url, init) => {
      if (init?.method === "POST") return jsonResponse({ id: 5 }, true, 201);
      return jsonResponse({ status: "pending" });
    });
    const { stream } = makeStream();
    setTimeout(() => ac.abort(), 5);
    await expect(
      gateAction({
        action: "publish_illustration",
        skillRules: [],
        sessionApprovals: new SessionApprovals(),
        runId: "7",
        novelId: 11,
        authHeader: "Bearer x",
        baseUrl: BASE,
        fetchImpl,
        stream,
        signal: ac.signal,
        intervalMs: 10,
      }),
    ).rejects.toBeInstanceOf(PolicyDenied);
  });
});
