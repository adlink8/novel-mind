/**
 * 域工具注册表（27-05 / REQ-WM-01..03 / D-05）——世界模型只读域。
 *
 * 拆分自 ``tools/registry.ts``（registry 保留为 re-export 兼容层）：本模块持有
 * Phase 27 的 5 个世界模型只读工具（get_events / get_character_state /
 * get_character_knowledge / get_world_rules / get_evidence_span）。每个工具都是
 * 显式 ``defineTool``，参数用 TypeBox ``Type.Object`` 镜像后端请求 schema；
 * ``execute`` 转发到 FastAPI 门面（version/cutoff/POV 服务端强制，D-05）。
 */

import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fastapiToolCall } from "../fastapi-client.js";
import type { ToolAuth } from "../registry.js";

// ── Phase 27 世界模型只读工具（27-05）──
export function buildWorldModelTools(auth: ToolAuth, runNovelId: number) {
  return [
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
        fastapiToolCall("get_events", params as unknown, signal, auth, runNovelId),
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
        fastapiToolCall("get_character_state", params as unknown, signal, auth, runNovelId),
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
        fastapiToolCall("get_character_knowledge", params as unknown, signal, auth, runNovelId),
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
        fastapiToolCall("get_world_rules", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "get_evidence_span",
      label: "Get Evidence Span",
      description:
        "物化 leaf 证据跨度（D-07/D-08）。首选：携带 search_novel_text 命中行的 chapter_id + chunk_id（服务端在原文中定位 chunk、确定性推导 offsets，无需自行数字符）；备选：chapter_id + source_start/source_end 手动 offsets。content_hash 可选：省略时服务端计算并返回。只返回冻结原文切片。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        chapter_id: Type.Integer({ minimum: 1, description: "数据库章节 ID（search 命中行的 chapter_id 字段原样传入；不是章节序号 chapter_number）" }),
        chunk_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "search_novel_text 命中行的 chunk ID（提供时无需 offsets）" }),
        ),
        source_start: Type.Optional(
          Type.Integer({ minimum: 0, description: "切片起点（含；chunk_id 缺省时必填）" }),
        ),
        source_end: Type.Optional(
          Type.Integer({ minimum: 1, description: "切片终点（不含；chunk_id 缺省时必填）" }),
        ),
        content_hash: Type.Optional(
          Type.String({
            minLength: 64,
            maxLength: 64,
            pattern: "^[0-9a-f]{64}$",
            description: "该切片内容的 SHA-256（可选；省略时服务端计算）",
          }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_evidence_span", params as unknown, signal, auth, runNovelId),
    }),
  ];
}
