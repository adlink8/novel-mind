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
        chapter_id: Type.Integer({ minimum: 1, description: "数据库章节 ID（search 命中行的 chapter_id 字段原样传入；不是章节序号 chapter_number）" }),
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
        top_k: Type.Optional(
          Type.Integer({ minimum: 1, maximum: 50, description: "返回结果数量上限" }),
        ),
        mode: Type.Optional(
          Type.Union(
            [
              Type.Literal("auto"),
              Type.Literal("chunks"),
              Type.Literal("units"),
              Type.Literal("hybrid"),
            ],
            { description: "检索意图（默认 auto，由服务端 router 决策）" },
          ),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("search_novel_text", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "get_timeline",
      label: "Get Timeline",
      description:
        "时间线事件视图。剧透截止点由服务端 resolve_chapter_cutoff 决定（防剧透）。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        ordering: Type.Optional(
          Type.Union([Type.Literal("narrative"), Type.Literal("story")], {
            description: "排序方式（默认 narrative）",
          }),
        ),
        person: Type.Optional(Type.String({ description: "人物过滤" })),
        causal: Type.Optional(Type.Boolean({ description: "是否包含因果边" })),
        chapter_start: Type.Optional(
          Type.Integer({ minimum: 1, description: "起始章节（含）" }),
        ),
        chapter_end: Type.Optional(
          Type.Integer({ minimum: 1, description: "结束章节（含）" }),
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
        character_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "按角色过滤" }),
        ),
        relation_type: Type.Optional(
          Type.String({ description: "按关系类型过滤" }),
        ),
        through_chapter: Type.Optional(
          Type.Integer({ minimum: 1, description: "可见性截止章节" }),
        ),
        version_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "显式版本 ID" }),
        ),
        include_provisional: Type.Optional(
          Type.Boolean({ description: "是否包含 provisional 关系" }),
        ),
        source: Type.Optional(Type.String({ description: "按来源过滤" })),
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
        character_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "按角色过滤" }),
        ),
        status: Type.Optional(Type.String({ description: "按线索状态过滤" })),
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
        version_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "显式版本 ID" }),
        ),
        view: Type.Optional(
          Type.Union([Type.Literal("versions"), Type.Literal("tree")], {
            description: "versions=版本列表；tree=指定版本的结构树",
          }),
        ),
        through_chapter: Type.Optional(
          Type.Integer({ minimum: 1, description: "可见性截止章节" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_narrative_memory", params as unknown, signal, auth, runNovelId),
    }),
  ];
}
