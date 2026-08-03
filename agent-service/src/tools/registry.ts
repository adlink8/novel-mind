/**
 * 域工具注册表（25.2-05 / D-06 / REQ-AGENT-01；27-05 扩展 Phase 27 世界模型工具）。
 *
 * 注册 25.2-02 facade 对应的 12 个只读域工具（7 个既有 + 5 个 Phase 27
 * 世界模型工具：get_events / get_character_state / get_character_knowledge /
 * get_world_rules / get_evidence_span）。每个工具都是显式 `defineTool`，
 * 参数用 TypeBox `Type.Object` 镜像后端请求 schema；`execute` 使用 Pi
 * 0.83.0 的五参签名 `(toolCallId, params, signal, onUpdate, ctx)`，转发到 FastAPI
 * 门面（owner / 剧透截止点 / budget / 字节上限在服务端强制执行，D-07）。
 *
 * `DOMAIN_TOOL_NAMES` 是工具 allowlist 的唯一事实源：会话工厂的 `tools` 数组、
 * skill loader 的 allowed_tools 校验都消费它，三者无法漂移。
 */

import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fastapiToolCall } from "./fastapi-client.js";

/** 12 个域工具名（固定顺序；唯一 allowlist 事实源）。 */
export const DOMAIN_TOOL_NAMES = [
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
] as const;

/** 工具注册时的运行级授权（端用户 JWT 或 per-run 内部令牌）。 */
export type ToolAuth = string;

/**
 * 构建 7 个域工具数组，携带给定的运行级授权（每次 run 分发时以 per-run 内部令牌调用）。
 */
export function buildDomainTools(auth: ToolAuth) {
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
        fastapiToolCall("get_novel", params as unknown, signal, auth),
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
        fastapiToolCall("get_chapter", params as unknown, signal, auth),
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
        fastapiToolCall("search_novel_text", params as unknown, signal, auth),
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
        fastapiToolCall("get_timeline", params as unknown, signal, auth),
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
        fastapiToolCall("get_relationships", params as unknown, signal, auth),
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
        fastapiToolCall("get_clues", params as unknown, signal, auth),
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
        fastapiToolCall("get_narrative_memory", params as unknown, signal, auth),
    }),
    // ── Phase 27 世界模型只读工具（27-05）──
    defineTool({
      name: "get_events",
      label: "Get World-Model Events",
      description:
        "世界模型事件/因果候选投影（REQ-WM-01）。version/cutoff 服务端强制（D-05）；无投影 404-hide。返回候选数据，绝不作为事实。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        version_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "世界模型版本 ID（缺省取最新）" }),
        ),
        cutoff: Type.Optional(
          Type.Integer({ minimum: 1, description: "截止章节（D-05）" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_events", params as unknown, signal, auth),
    }),
    defineTool({
      name: "get_character_state",
      label: "Get Character State",
      description:
        "角色状态/目标/动机（REQ-WM-02，aspect ∈ state/goal/motivation）。cutoff/POV 服务端强制（D-05）；无可见声明 abstained，绝不编造。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        subject: Type.String({ minLength: 1, description: "角色名" }),
        version_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "世界模型版本 ID（缺省取最新）" }),
        ),
        cutoff: Type.Optional(
          Type.Integer({ minimum: 1, description: "截止章节（D-05）" }),
        ),
        pov: Type.Optional(
          Type.String({ minLength: 1, description: "视角过滤（POV）" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_character_state", params as unknown, signal, auth),
    }),
    defineTool({
      name: "get_character_knowledge",
      label: "Get Character Knowledge",
      description:
        "角色知识（REQ-WM-02，aspect=knowledge）。mistaken/hidden 保持显式标签（D-05），绝不静默升级为事实。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        subject: Type.String({ minLength: 1, description: "角色名" }),
        version_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "世界模型版本 ID（缺省取最新）" }),
        ),
        cutoff: Type.Optional(
          Type.Integer({ minimum: 1, description: "截止章节（D-05）" }),
        ),
        pov: Type.Optional(
          Type.String({ minLength: 1, description: "视角过滤（POV）" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_character_knowledge", params as unknown, signal, auth),
    }),
    defineTool({
      name: "get_world_rules",
      label: "Get World Rules",
      description:
        "世界规则与规则例外（REQ-WM-03）。例外是 first-class 记录（D-04）；cutoff 服务端强制（D-05）。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        version_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "世界模型版本 ID（缺省取最新）" }),
        ),
        cutoff: Type.Optional(
          Type.Integer({ minimum: 1, description: "截止章节（D-05）" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_world_rules", params as unknown, signal, auth),
    }),
    defineTool({
      name: "get_evidence_span",
      label: "Get Evidence Span",
      description:
        "按 chapter+offsets+content_hash 物化 leaf 证据跨度（D-07/D-08）。只返回冻结原文切片；hash 不匹配或超截止点拒绝。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        chapter_id: Type.Integer({ minimum: 1, description: "章节 ID" }),
        source_start: Type.Integer({ minimum: 0, description: "切片起点（含）" }),
        source_end: Type.Integer({ minimum: 1, description: "切片终点（不含）" }),
        content_hash: Type.String({
          minLength: 64,
          maxLength: 64,
          pattern: "^[0-9a-f]{64}$",
          description: "该切片内容的 SHA-256",
        }),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_evidence_span", params as unknown, signal, auth),
    }),
  ];
}
