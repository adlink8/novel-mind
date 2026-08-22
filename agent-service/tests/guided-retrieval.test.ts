/**
 * guided-retrieval 单元测试（Slice 1）。
 *
 * seam：deriveQueries 的纯函数行为 + guidedRetrieve 经注入 ToolCaller 的
 * 检索/物化流程。关键纪律：
 * - 菜单只含编号/章节/摘录，绝不含 evidence_key；
 * - 物化失败的行诚实跳过；全失败 → 抛错（fail closed）；
 * - evidences 与 agentic 路径的 get_evidence_span 工具结果同构（投影直接复用）。
 */

import { describe, it, expect } from "vitest";
import {
  deriveQueries,
  guidedRetrieve,
  type ToolCaller,
} from "../src/guided/retrieval.js";

const HASH = "a".repeat(64);

function spanText(chunkId: number, chapterNumber: number): string {
  return JSON.stringify({
    evidence_key: `qp:7:0:10:${HASH.slice(0, 8)}${chunkId}`,
    chapter_id: 7,
    chapter_number: chapterNumber,
    novel_id: 6,
    source_start: 0,
    source_end: 10,
    content_hash: HASH,
    excerpt: `第${chapterNumber}章的摘录文本`,
  });
}

function searchPayload(rows: unknown[]): string {
  return JSON.stringify({ results: rows, resolved_mode: "chunks", fallback_reason: null });
}

/** fake ToolCaller：按调用参数路由到预设响应。 */
function fakeCaller(handlers: {
  searchRows?: unknown[];
  spanOkChunkIds?: number[];
  failSearch?: boolean;
}): ToolCaller {
  return async (name, params) => {
    if (name === "search_novel_text") {
      if (handlers.failSearch) throw new Error("timeout");
      return {
        content: [{ type: "text", text: searchPayload(handlers.searchRows ?? []) }],
      };
    }
    if (name === "get_evidence_span") {
      const p = params as { chunk_id?: number };
      if ((handlers.spanOkChunkIds ?? []).includes(p.chunk_id ?? -1)) {
        return {
          content: [{ type: "text", text: spanText(p.chunk_id!, 51) }],
        };
      }
      throw new Error("beyond_cutoff");
    }
    throw new Error(`unexpected tool ${name}`);
  };
}

describe("deriveQueries", () => {
  it("短问题整句作为检索词", () => {
    expect(deriveQueries("死城")).toEqual(["死城"]);
  });

  it("长问题去停用词后 bigram 扇出", () => {
    const queries = deriveQueries("死城的环境和景象是什么样的？");
    expect(queries).toContain("死城");
    expect(queries).toContain("环境");
    expect(queries).toContain("景象");
    expect(queries.length).toBeLessThanOrEqual(8);
    // 停用词不得残留
    for (const q of queries) {
      expect(q).not.toMatch(/[的吗呢什]/);
    }
  });

  it("纯停用词问题 → 空（调用方 fail closed）", () => {
    expect(deriveQueries("是什么样的呢？")).toEqual([]);
  });
});

describe("guidedRetrieve", () => {
  it("检索→去重→物化→编号菜单（菜单绝不含 evidence_key）", async () => {
    const result = await guidedRetrieve({
      question: "死城的环境",
      auth: "Bearer t",
      novelId: 6,
      callTool: fakeCaller({
        searchRows: [
          { chunk_id: 11, chapter_id: 7, chapter_number: 51, score: 0.9 },
          { chunk_id: 12, chapter_id: 8, chapter_number: 52, score: 0.7 },
          { chunk_id: 11, chapter_id: 7, chapter_number: 51, score: 0.3 }, // 重复命中
        ],
        spanOkChunkIds: [11, 12],
      }),
    });

    expect(result.menu).toHaveLength(2);
    expect(result.menu[0].index).toBe(1);
    expect(result.menu[1].index).toBe(2);
    expect(result.menu[0].chapter_number).toBe(51);
    expect(result.menu[0].excerpt).toContain("摘录");
    // 菜单任何字段都不得泄露 evidence_key
    expect(JSON.stringify(result.menu)).not.toContain("qp:");
    // 程序侧 keys 与 menu 同序
    expect(result.keys).toHaveLength(2);
    expect(result.keys[0]).toMatch(/^qp:/);
    // evidences 与 agentic 路径工具结果同构（可被 collectRuntimeSpans 消费）
    expect(result.evidences).toHaveLength(2);
    expect(result.evidences[0].toolName).toBe("get_evidence_span");
    const span = JSON.parse(result.evidences[0].content);
    expect(span.evidence_key).toBe(result.keys[0]);
  });

  it("物化失败的行诚实跳过（beyond_cutoff / 漂移）", async () => {
    const result = await guidedRetrieve({
      question: "死城",
      auth: "Bearer t",
      novelId: 6,
      callTool: fakeCaller({
        searchRows: [
          { chunk_id: 11, chapter_id: 7, chapter_number: 51, score: 0.9 },
          { chunk_id: 99, chapter_id: 9, chapter_number: 99, score: 0.8 }, // 将物化失败
        ],
        spanOkChunkIds: [11],
      }),
    });
    expect(result.menu).toHaveLength(1);
    expect(result.menu[0].chapter_number).toBe(51);
  });

  it("chapter 0（前言页）span 直接跳过，不进菜单", async () => {
    const caller: ToolCaller = async (name, params) => {
      if (name === "search_novel_text") {
        return {
          content: [{ type: "text", text: searchPayload([
            { chunk_id: 5, chapter_id: 6, chapter_number: 0, score: 0.9 },
            { chunk_id: 11, chapter_id: 7, chapter_number: 51, score: 0.8 },
          ]) }],
        };
      }
      const p = params as { chunk_id?: number };
      // 注意：span 响应里的 chapter_number 以物化结果为准（chunk 5 → 0）
      return {
        content: [{ type: "text", text: spanText(p.chunk_id!, p.chunk_id === 5 ? 0 : 51) }],
      };
    };
    const result = await guidedRetrieve({
      question: "死城",
      auth: "Bearer t",
      novelId: 6,
      callTool: caller,
    });
    expect(result.menu).toHaveLength(1);
    expect(result.menu[0].chapter_number).toBe(51);
  });

  it("零命中 / 全部物化失败 → fail closed", async () => {
    await expect(
      guidedRetrieve({
        question: "死城",
        auth: "Bearer t",
        novelId: 6,
        callTool: fakeCaller({ searchRows: [] }),
      }),
    ).rejects.toThrow(/no evidence materialized/);

    await expect(
      guidedRetrieve({
        question: "死城",
        auth: "Bearer t",
        novelId: 6,
        callTool: fakeCaller({
          searchRows: [{ chunk_id: 11, chapter_id: 7, chapter_number: 51, score: 0.9 }],
          spanOkChunkIds: [],
        }),
      }),
    ).rejects.toThrow(/no evidence materialized/);
  });

  it("检索词全部失败 → fail closed（不伪造证据）", async () => {
    await expect(
      guidedRetrieve({
        question: "死城",
        auth: "Bearer t",
        novelId: 6,
        callTool: fakeCaller({ failSearch: true }),
      }),
    ).rejects.toThrow(/no evidence materialized/);
  });

  it("无检索词（纯停用词问题）→ fail closed", async () => {
    await expect(
      guidedRetrieve({
        question: "是什么样的呢？",
        auth: "Bearer t",
        novelId: 6,
        callTool: fakeCaller({}),
      }),
    ).rejects.toThrow(/no search terms/);
  });
});
