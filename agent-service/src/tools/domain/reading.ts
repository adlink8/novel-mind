/**
 * 域工具注册表（25.2-05 / D-06 / REQ-AGENT-01）——阅读域（novel-read）。
 *
 * 拆分自 ``tools/registry.ts``（registry 保留为 re-export 兼容层）：本模块持有
 * 7 个只读阅读域工具（get_novel / get_chapter / search_novel_text / get_timeline /
 * get_relationships / get_clues / get_narrative_memory）。每个工具都是显式
 * ``defineTool``，参数用 TypeBox ``Type.Object`` 镜像后端请求 schema；``execute``
 * 使用 Pi 0.83.0 的五参签名 ``(toolCallId, params, signal, onUpdate, ctx)``，
 * 转发到 FastAPI 门面（owner / 剧透截止点 / budget / 字节上限在服务端强制执行，D-07）。
 */

import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fastapiToolCall } from "../fastapi-client.js";
import type { ToolAuth } from "../registry.js";

export function buildReadingTools(auth: ToolAuth, runNovelId: number) {
  return [
    defineTool({
      name: "get_novel",
      label: "Get Novel",
      description:
        "获取小说详情（含章节列表）。owner 校验在服务端强制执行，非本人小说一律 404。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_novel", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "get_chapter",
      label: "Get Chapter",
      description:
        "获取章节正文全文。owner 校验 + 剧透截止点（beyond_cutoff）在服务端强制执行。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        chapter_id: Type.Integer({ minimum: 1, description: "章节 ID" }),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_chapter", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "search_novel_text",
      label: "Search Novel Text",
      description:
        "全文搜索小说正文（raw + narrative unit，ADR-0002）。命中片段带证据跨度，可作 evidence_ref。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        query: Type.String({ minLength: 1, description: "搜索关键词" }),
        limit: Type.Optional(
          Type.Integer({ minimum: 1, maximum: 50, description: "返回上限" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("search_novel_text", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "get_timeline",
      label: "Get Timeline",
      description:
        "时间线事件视图。full_book 仅当用户持久化开关开启才被服务端采纳（防剧透）。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        full_book: Type.Optional(
          Type.Boolean({ description: "仅当持久化全量开关开启时被采纳" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_timeline", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "get_relationships",
      label: "Get Relationships",
      description:
        "角色关系图。owner 校验 + 剧透截止点服务端强制执行。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        full_book: Type.Optional(
          Type.Boolean({ description: "仅当持久化全量开关开启时被采纳" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_relationships", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "get_clues",
      label: "Get Clues",
      description:
        "线索信封（可见性按事件重放推导）。owner 校验服务端强制执行。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        full_book: Type.Optional(
          Type.Boolean({ description: "仅当持久化全量开关开启时被采纳" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_clues", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "get_narrative_memory",
      label: "Get Narrative Memory",
      description:
        "叙事记忆结构查询（版本/树/主张）。返回候选数据（release_status: candidate，ADR-0002）——绝不作为事实。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        query: Type.String({ minLength: 1, description: "结构化查询语句" }),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_narrative_memory", params as unknown, signal, auth, runNovelId),
    }),
  ];
}
