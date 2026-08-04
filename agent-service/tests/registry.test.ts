import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { Check } from "typebox/value";
import {
  DOMAIN_TOOL_NAMES,
  buildDomainTools,
} from "../src/tools/registry.js";
import {
  AGENT_TOOL_ERROR_CODES,
  fastapiToolCall,
  TOOL_OUTPUT_BYTE_LIMIT,
} from "../src/tools/fastapi-client.js";

/**
 * registry.test.ts（25.2-05 Task 1 + 27-05 Phase 27 世界模型工具 + 31-04 get_visual_bible
 * + 33-05 generate_image_candidate + 34-05 publish_illustration/attach_illustration_to_text）：
 * - 16 个工具注册、TypeBox 参数拒绝错误类型
 * - facade 错误信封 → 冻结 AGENT_TOOL_ERRORS 精确镜像
 * - 64 KiB+1 字节 → output_too_large
 * - 运行级 abort 取消 in-flight fetch
 * - DOMAIN_TOOL_NAMES 与构建结果单一事实源，无法漂移
 * 全部使用 mock fetch，不真连 backend。
 */

/** 16 个域工具名（测试侧镜像，与 DOMAIN_TOOL_NAMES 断言一致；31-04 加入
 * get_visual_bible，33-05 加入 generate_image_candidate，34-05 加入
 * publish_illustration / attach_illustration_to_text action）。 */
const TOOL_NAMES_16 = [
  "get_novel",
  "get_chapter",
  "search_novel_text",
  "get_timeline",
  "get_relationships",
  "get_clues",
  "get_narrative_memory",
  "get_events",
  "get_character_state",
  "get_character_knowledge",
  "get_world_rules",
  "get_evidence_span",
  "get_visual_bible",
  "generate_image_candidate",
  "publish_illustration",
  "attach_illustration_to_text",
] as const;

describe("domain tool registry", () => {
  it("DOMAIN_TOOL_NAMES 恰为 16 个冻结工具名", () => {
    expect([...DOMAIN_TOOL_NAMES]).toEqual([...TOOL_NAMES_16]);
  });

  it("buildDomainTools 返回 16 个 defineTool，名称与 DOMAIN_TOOL_NAMES 一致", () => {
    const tools = buildDomainTools("Bearer per-run-token");
    expect(tools).toHaveLength(16);
    const names = tools.map((t) => t.name);
    expect(names).toEqual([...TOOL_NAMES_16]);
    // 每个工具都是 defineTool（含 execute 五参签名）
    for (const tool of tools) {
      expect(typeof tool.execute).toBe("function");
      expect(tool.parameters).toBeDefined();
    }
  });

  it("每个工具的参数 schema 拒绝错误类型（TypeBox/value）", () => {
    const tools = buildDomainTools("Bearer t");
    const byName = new Map(tools.map((t) => [t.name, t.parameters]));

    // 正整数域：传字符串/小数/负数均拒绝
    const novelId = { novel_id: 3 };
    expect(Check(byName.get("get_novel")!, novelId)).toBe(true);
    expect(Check(byName.get("get_novel")!, { novel_id: "3" })).toBe(false);
    expect(Check(byName.get("get_novel")!, { novel_id: -1 })).toBe(false);
    expect(Check(byName.get("get_novel")!, {})).toBe(false);

    // get_chapter：novel_id + chapter_id 必填正整数
    const chapter = byName.get("get_chapter")!;
    expect(Check(chapter, { novel_id: 1, chapter_id: 2 })).toBe(true);
    expect(Check(chapter, { novel_id: 1, chapter_id: "two" })).toBe(false);
    expect(Check(chapter, { novel_id: 1 })).toBe(false);

    // search_novel_text：query 必填非空字符串；limit 可选正整数
    const search = byName.get("search_novel_text")!;
    expect(Check(search, { novel_id: 1, query: "竹林" })).toBe(true);
    expect(Check(search, { novel_id: 1, query: "竹林", limit: 5 })).toBe(true);
    expect(Check(search, { novel_id: 1, query: "" })).toBe(false);
    expect(Check(search, { novel_id: 1, query: "x", limit: 0 })).toBe(false);

    // get_narrative_memory：query 必填
    const nm = byName.get("get_narrative_memory")!;
    expect(Check(nm, { novel_id: 1, query: "人物关系" })).toBe(true);
    expect(Check(nm, { novel_id: 1 })).toBe(false);
  });
});

describe("fastapi client (facade forwarding)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  const AUTH = "Bearer per-run-internal-token";

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("转发 POST /api/agent-tools/{name}，authorization 原样透传", async () => {
    fetchMock.mockResolvedValue(
      new Response('{"data":"ok"}', { status: 200, headers: { "content-type": "application/json" } }),
    );
    const result = await fastapiToolCall("get_chapter", { novel_id: 1, chapter_id: 2 }, undefined, AUTH);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/agent-tools/get_chapter");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ authorization: AUTH });
    expect(JSON.parse(String(init.body))).toEqual({ novel_id: 1, chapter_id: 2 });
    expect(result.content[0].text).toBe('{"data":"ok"}');
  });

  it("409/422 信封 code=beyond_cutoff 精确浮现该错误码", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "beyond_cutoff" } }), { status: 422 }),
    );
    await expect(
      fastapiToolCall("get_chapter", { novel_id: 1, chapter_id: 99 }, undefined, AUTH),
    ).rejects.toMatchObject({ code: "beyond_cutoff" });
  });

  it("错误信封 code 集合恰为冻结的 AGENT_TOOL_ERRORS（单一事实源断言）", async () => {
    // 逐个 code 打洞：每个冻结错误码都必须从 facade 信封浮现为同一 code
    for (const code of AGENT_TOOL_ERROR_CODES) {
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code } }), { status: 400 }),
      );
      await expect(
        fastapiToolCall("get_novel", { novel_id: 1 }, undefined, AUTH),
      ).rejects.toMatchObject({ code });
    }
  });

  it("未知错误码归一为 upstream_error（不发明 agent-service 自有码）", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "mystery_code" } }), { status: 418 }),
    );
    await expect(
      fastapiToolCall("get_novel", { novel_id: 1 }, undefined, AUTH),
    ).rejects.toMatchObject({ code: "upstream_error" });
  });

  it("响应体超 64 KiB → output_too_large，先于解析", async () => {
    const oversized = "x".repeat(TOOL_OUTPUT_BYTE_LIMIT + 1);
    fetchMock.mockResolvedValue(new Response(oversized, { status: 200 }));
    await expect(
      fastapiToolCall("get_novel", { novel_id: 1 }, undefined, AUTH),
    ).rejects.toMatchObject({ code: "output_too_large" });
  });

  it("运行级 abort 取消 in-flight fetch（不转成错误码，向上传播）", async () => {
    const ctrl = new AbortController();
    fetchMock.mockImplementation((_url: string, init: RequestInit) => {
      // 挂载真实 signal：外部 abort 即 reject
      const signal = init.signal as AbortSignal;
      return new Promise((_resolve, reject) => {
        const onAbort = () => reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
        if (signal.aborted) onAbort();
        else signal.addEventListener("abort", onAbort, { once: true });
      });
    });

    const promise = fastapiToolCall("get_novel", { novel_id: 1 }, ctrl.signal, AUTH);
    ctrl.abort();
    await expect(promise).rejects.toThrow();
  });

  it("fetch TimeoutError（AbortSignal.timeout 触发）→ timeout 错误码", async () => {
    fetchMock.mockRejectedValue(
      new DOMException("The operation was aborted due to timeout", "TimeoutError"),
    );
    await expect(
      fastapiToolCall("get_novel", { novel_id: 1 }, undefined, AUTH),
    ).rejects.toMatchObject({ code: "timeout" });
  });
});
