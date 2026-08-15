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
 * + 33-05 generate_image_candidate + 34-05 publish_illustration/attach_illustration_to_text
 * + 35-05 create_canon_fork + 36-05 apply_derivative_edit + 37-05
 * allow_divergence/publish_derivative_revision）：
 * - 20 个工具注册、TypeBox 参数拒绝错误类型
 * - facade 错误信封 → 冻结 AGENT_TOOL_ERRORS 精确镜像
 * - 64 KiB+1 字节 → output_too_large
 * - 运行级 abort 取消 in-flight fetch
 * - DOMAIN_TOOL_NAMES 与构建结果单一事实源，无法漂移
 * 全部使用 mock fetch，不真连 backend。
 */

/** 23 个域工具名（测试侧镜像，与 DOMAIN_TOOL_NAMES 断言一致；31-04 加入
 * get_visual_bible，33-05 加入 generate_image_candidate，34-05 加入
 * publish_illustration / attach_illustration_to_text action，35-05 加入
 * create_canon_fork action，36-05 加入 apply_derivative_edit action，37-05 加入
 * allow_divergence / publish_derivative_revision action，38-05 加入
 * publish_derivative_visual action，39-05 加入 approve_export /
 * materialize_export action）。 */
const TOOL_NAMES_23 = [
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
  "create_canon_fork",
  "apply_derivative_edit",
  "allow_divergence",
  "publish_derivative_revision",
  "publish_derivative_visual",
  "approve_export",
  "materialize_export",
] as const;

describe("domain tool registry", () => {
  it("DOMAIN_TOOL_NAMES 恰为 23 个冻结工具名", () => {
    expect([...DOMAIN_TOOL_NAMES]).toEqual([...TOOL_NAMES_23]);
  });

  it("buildDomainTools 返回 23 个 defineTool，名称与 DOMAIN_TOOL_NAMES 一致", () => {
    const tools = buildDomainTools("Bearer per-run-token", 1);
    expect(tools).toHaveLength(23);
    const names = tools.map((t) => t.name);
    expect(names).toEqual([...TOOL_NAMES_23]);
    // 每个工具都是 defineTool（含 execute 五参签名）
    for (const tool of tools) {
      expect(typeof tool.execute).toBe("function");
      expect(tool.parameters).toBeDefined();
    }
  });

  it("每个工具的参数 schema 拒绝错误类型（TypeBox/value）", () => {
    const tools = buildDomainTools("Bearer t", 1);
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

    // search_novel_text：query 必填非空字符串；top_k/mode 可选（镜像后端
    // SearchNovelTextRequest，extra="forbid"——limit 等漂移字段 → 后端 422）
    const search = byName.get("search_novel_text")!;
    expect(Check(search, { novel_id: 1, query: "竹林" })).toBe(true);
    expect(Check(search, { novel_id: 1, query: "竹林", top_k: 5 })).toBe(true);
    expect(Check(search, { novel_id: 1, query: "竹林", mode: "auto" })).toBe(true);
    expect(Check(search, { novel_id: 1, query: "" })).toBe(false);
    expect(Check(search, { novel_id: 1, query: "x", top_k: 0 })).toBe(false);

    // get_narrative_memory：全部参数可选（版本/树/截止章视图）
    const nm = byName.get("get_narrative_memory")!;
    expect(Check(nm, { novel_id: 1 })).toBe(true);
    expect(Check(nm, { novel_id: 1, view: "tree", version_id: 2 })).toBe(true);
    expect(Check(nm, { novel_id: 1, view: "bogus" })).toBe(false);
  });

  it("get_evidence_span：content_hash 可选，提供时仍校验 64-hex 格式", () => {
    const tools = buildDomainTools("Bearer t", 1);
    const byName = new Map(tools.map((t) => [t.name, t.parameters]));
    const span = byName.get("get_evidence_span")!;

    // 省略 content_hash → 接受（服务端计算返回，模型无需自行算 hash）
    expect(
      Check(span, { novel_id: 1, chapter_id: 2, source_start: 0, source_end: 40 }),
    ).toBe(true);
    // 提供合法 64-hex → 接受（服务端校验匹配，防漂移）
    expect(
      Check(span, {
        novel_id: 1,
        chapter_id: 2,
        source_start: 0,
        source_end: 40,
        content_hash: "a".repeat(64),
      }),
    ).toBe(true);
    // 提供非法格式 → 拒绝
    expect(
      Check(span, {
        novel_id: 1,
        chapter_id: 2,
        source_start: 0,
        source_end: 40,
        content_hash: "not-a-hash",
      }),
    ).toBe(false);
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
    const result = await fastapiToolCall("get_chapter", { novel_id: 1, chapter_id: 2 }, undefined, AUTH, 1);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/agent-tools/get_chapter?novel_id=1"
    );
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ authorization: AUTH });
    expect(JSON.parse(String(init.body))).toEqual({ chapter_id: 2 });
    expect(result.content[0].text).toBe('{"data":"ok"}');
  });

  it("409/422 信封 code=beyond_cutoff 精确浮现该错误码", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "beyond_cutoff" } }), { status: 422 }),
    );
    await expect(
      fastapiToolCall("get_chapter", { novel_id: 1, chapter_id: 99 }, undefined, AUTH, 1),
    ).rejects.toMatchObject({ code: "beyond_cutoff" });
  });

  it("FastAPI 原生 422（pydantic detail 数组）→ invalid_input 且带字段明细", async () => {
    // 后端 StrictAgentToolModel extra="forbid" / 必填缺失时，FastAPI 返回
    // 原生 RequestValidationError 信封（{detail: [...]}），没有 error.code。
    // 若归一为 upstream_error，模型拿不到可操作信息只能盲目重试（E2E 实测
    // get_evidence_span 连续 422 后模型放弃）；必须浮现 invalid_input +
    // 字段明细，repair/重试才有方向。
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            {
              type: "missing",
              loc: ["body", "source_start"],
              msg: "Field required",
            },
          ],
        }),
        { status: 422 },
      ),
    );
    const err = await fastapiToolCall(
      "get_evidence_span",
      { novel_id: 1, chapter_id: 7, source_end: 40 },
      undefined,
      AUTH,
      1,
    ).catch((e: unknown) => e);
    expect(err).toMatchObject({ code: "invalid_input" });
    expect((err as Error).message).toContain("source_start");
  });

  it("错误信封 code 集合恰为冻结的 AGENT_TOOL_ERRORS（单一事实源断言）", async () => {
    // 逐个 code 打洞：每个冻结错误码都必须从 facade 信封浮现为同一 code
    for (const code of AGENT_TOOL_ERROR_CODES) {
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify({ error: { code } }), { status: 400 }),
      );
      await expect(
        fastapiToolCall("get_novel", { novel_id: 1 }, undefined, AUTH, 1),
      ).rejects.toMatchObject({ code });
    }
  });

  it("未知错误码归一为 upstream_error（不发明 agent-service 自有码）", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "mystery_code" } }), { status: 418 }),
    );
    await expect(
      fastapiToolCall("get_novel", { novel_id: 1 }, undefined, AUTH, 1),
    ).rejects.toMatchObject({ code: "upstream_error" });
  });

  it("响应体超 64 KiB → output_too_large，先于解析", async () => {
    const oversized = "x".repeat(TOOL_OUTPUT_BYTE_LIMIT + 1);
    fetchMock.mockResolvedValue(new Response(oversized, { status: 200 }));
    await expect(
      fastapiToolCall("get_novel", { novel_id: 1 }, undefined, AUTH, 1),
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

    const promise = fastapiToolCall("get_novel", { novel_id: 1 }, ctrl.signal, AUTH, 1);
    ctrl.abort();
    await expect(promise).rejects.toThrow();
  });

  it("fetch TimeoutError（AbortSignal.timeout 触发）→ timeout 错误码", async () => {
    fetchMock.mockRejectedValue(
      new DOMException("The operation was aborted due to timeout", "TimeoutError"),
    );
    await expect(
      fastapiToolCall("get_novel", { novel_id: 1 }, undefined, AUTH, 1),
    ).rejects.toMatchObject({ code: "timeout" });
  });
});
