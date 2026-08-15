/**
 * guided 检索模块（「确定性检索 + 单轮生成」改造 Slice 1）。
 *
 * 契约：
 * - deriveQueries：从问题提取检索词（去停用词后 bigram 扇出，确定性、无模型
 *   参与）；问题本身 ≤4 字时直接用整句。
 * - guidedRetrieve：对每个检索词调 search_novel_text（经工具门面，cutoff/owner
 *   门照常生效），按 chunk_id 去重、跨词累计 score 排序，取 top N 用 chunk_id
 *   物化成 span；物化失败（beyond_cutoff / not_found / 索引漂移）的行诚实跳过，
 *   绝不伪造。全部失败/零命中 → 抛错（fail closed）。
 * - 返回的 menu 是给模型的编号证据菜单（只含序号/章节号/摘录，**绝不含
 *   evidence_key**）；evidences 是投影消费的运行时证据（ToolEvidence 形状，
 *   与 agentic 路径的 get_evidence_span 工具结果逐字节同构）。
 */

import { fastapiToolCall } from "../tools/fastapi-client.js";
import type { ToolEvidence } from "../tools/tool-evidence.js";

type JsonObject = Record<string, unknown>;

/** 单次工具调用 seam（默认走 FastAPI 门面；测试注入 fake）。 */
export type ToolCaller = (
  name: string,
  params: unknown,
  signal: AbortSignal | undefined,
  auth: string,
  runNovelId: number,
) => Promise<{ content: [{ type: "text"; text: string }] }>;

/** 菜单条目（模型可见；evidence_key 刻意不在其中）。 */
export interface GuidedMenuItem {
  index: number;
  chapter_number: number;
  excerpt: string;
}

export interface GuidedRetrieval {
  menu: GuidedMenuItem[];
  /** search_novel_text 实际调用次数（ToolRun 血缘用）。 */
  searchCalls: number;
  /** 与 menu 同序的 evidence_key（仅程序侧使用，绝不进 prompt）。 */
  keys: string[];
  /** 投影消费的运行时证据（get_evidence_span 物化结果）。 */
  evidences: ToolEvidence[];
}

const MAX_QUERIES = 8;
const MENU_SIZE = 5;
const EXCERPT_MAX = 500;

/** 问题清洗：停用词/标点/空白剔除（只保留实义字序列）。 */
const STOP_PATTERN =
  /(?:是什么|什么样|怎么样|怎样|样|如何|哪些|哪个|哪位|谁|为什么|为何|多少|是不是|有没有|知道|告诉|请问|描述一下|介绍一下|[的了吗呢吧啊嘛嘛噢哦呀哈嘛么哪很最非常特别十分实在太挺还又再在就和与跟被把让给向从按照根据关于对于以及而且或者如果因为所以虽然但是]|[？?！!，,。.、；;：:""''（）()\s])/g;

/** 从问题派生检索词（bigram 扇出；长度不足时整句）。 */
export function deriveQueries(question: string): string[] {
  const cleaned = question.replace(STOP_PATTERN, "");
  if (cleaned.length < 2) {
    // 单字残余（如"样"）无检索价值，按无词处理（fail closed）。
    return [];
  }
  if (cleaned.length <= 4) {
    return [cleaned];
  }
  const bigrams: string[] = [];
  for (let i = 0; i + 2 <= cleaned.length; i += 1) {
    bigrams.push(cleaned.slice(i, i + 2));
  }
  return [...new Set(bigrams)].slice(0, MAX_QUERIES);
}

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** 解析工具文本结果为 JSON 对象（失败 → null，诚实跳过）。 */
function parseToolJson(text: string): JsonObject | null {
  try {
    const parsed: unknown = JSON.parse(text);
    return isObject(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

interface SearchRow {
  chunk_id: number;
  chapter_id: number;
  chapter_number: number | null;
  score: number;
}

/** 聚合检索命中：chunk_id 去重，跨检索词累计 score。 */
function mergeSearchRows(
  perQuery: JsonObject[],
): SearchRow[] {
  const byChunk = new Map<number, SearchRow>();
  for (const payload of perQuery) {
    const rows = Array.isArray(payload.results) ? payload.results : [];
    for (const raw of rows) {
      if (!isObject(raw)) continue;
      const chunkId = raw.chunk_id;
      const chapterId = raw.chapter_id;
      if (!Number.isInteger(chunkId) || !Number.isInteger(chapterId)) continue;
      const score = typeof raw.score === "number" ? raw.score : 0;
      const chapterNumber = Number.isInteger(raw.chapter_number)
        ? (raw.chapter_number as number)
        : null;
      const prior = byChunk.get(chunkId as number);
      if (prior) {
        prior.score += score;
      } else {
        byChunk.set(chunkId as number, {
          chunk_id: chunkId as number,
          chapter_id: chapterId as number,
          chapter_number: chapterNumber,
          score,
        });
      }
    }
  }
  return [...byChunk.values()]
    .sort((a, b) => b.score - a.score)
    .slice(0, MENU_SIZE);
}

/**
 * 确定性检索 + 物化：返回编号菜单与投影证据。
 *
 * 任何工具错误（超时/超预算/beyond_cutoff/索引漂移）都只跳过该行；
 * 菜单为空 → 抛错（调用方让 run 诚实失败，绝不伪造证据继续）。
 */
export async function guidedRetrieve(opts: {
  question: string;
  auth: string;
  novelId: number;
  callTool?: ToolCaller;
  signal?: AbortSignal;
}): Promise<GuidedRetrieval> {
  const callTool = opts.callTool ?? fastapiToolCall;
  const queries = deriveQueries(opts.question);
  if (queries.length === 0) {
    throw new Error("guided-retrieval: question yields no search terms (fail closed)");
  }

  const perQuery: JsonObject[] = [];
  let searchCalls = 0;
  for (const query of queries) {
    searchCalls += 1;
    try {
      const res = await callTool(
        "search_novel_text",
        { query, top_k: 5 },
        opts.signal,
        opts.auth,
        opts.novelId,
      );
      const payload = parseToolJson(res.content[0]?.text ?? "");
      if (payload) perQuery.push(payload);
    } catch {
      // 单词检索失败（超时/限流）不致命：其余词继续。
    }
  }

  const rows = mergeSearchRows(perQuery);
  const menu: GuidedMenuItem[] = [];
  const keys: string[] = [];
  const evidences: ToolEvidence[] = [];
  for (const row of rows) {
    let spanText: string;
    try {
      const res = await callTool(
        "get_evidence_span",
        { chapter_id: row.chapter_id, chunk_id: row.chunk_id },
        opts.signal,
        opts.auth,
        opts.novelId,
      );
      spanText = res.content[0]?.text ?? "";
    } catch {
      continue; // beyond_cutoff / not_found / 索引漂移：诚实跳过该行
    }
    const span = parseToolJson(spanText);
    if (!span || typeof span.evidence_key !== "string") continue;
    const chapterNumber = Number.isInteger(span.chapter_number)
      ? (span.chapter_number as number)
      : row.chapter_number;
    if (chapterNumber === null) continue;
    // chapter 0 是前言/声明页：域契约 chapter_number >= 1，这类 span 永远
    // 过不了投影门，物化阶段直接跳过（E2E run 104 round 0 实测浪费一轮修复）。
    if (chapterNumber < 1) continue;
    const excerpt =
      typeof span.excerpt === "string" && span.excerpt
        ? span.excerpt.slice(0, EXCERPT_MAX)
        : "";
    menu.push({
      index: menu.length + 1,
      chapter_number: chapterNumber,
      excerpt,
    });
    keys.push(span.evidence_key);
    evidences.push({ toolName: "get_evidence_span", content: spanText });
  }

  if (menu.length === 0) {
    throw new Error(
      "guided-retrieval: no evidence materialized within cutoff (fail closed)",
    );
  }
  return { menu, keys, evidences, searchCalls };
}
