/**
 * 域工具注册表（34-05 / REQ-VIS-05 / REQ-AGENT-03/04/07 / D-34-01/02）——
 * 插图锚点提议 action 域。
 *
 * 拆分自 ``tools/registry.ts``（registry 保留为 re-export 兼容层）：本模块持有
 * Phase 34 的两个锚点提议 action 工具（publish_illustration /
 * attach_illustration_to_text）与私有参数 helper ``anchorActionParams``。
 * 每个工具都是显式 ``defineTool``，参数用 TypeBox ``Type.Object`` 镜像后端请求
 * schema；``execute`` 转发到 FastAPI 门面（proposal gate / payload_hash 确定性
 * 重放服务端强制，D-34-01）。
 */

import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fastapiToolCall } from "../fastapi-client.js";
import type { ToolAuth } from "../registry.js";

export function buildIllustrationTools(auth: ToolAuth, runNovelId: number) {
  return [
    // ── Phase 34 锚点提议 action 工具（34-05）──
    defineTool({
      name: "publish_illustration",
      label: "Publish Illustration (Proposal)",
      description:
        "Phase 34 action（REQ-VIS-05 / REQ-AGENT-03/04/07）：提议发布**一个**锚点（candidate-only）。服务端 proposal gate 只接受 proposal-ready + rights cleared 的 AssetRevision（Phase 33 handoff）与精确 source span（excerpt + anchor_hash + chapter_content_hash + source snapshot，D-34-01）；创建候选 IllustrationAnchorProposal + pending Web ApprovalRequest（action=publish_illustration，payload_hash 确定性重放）。绝不发布——确定性 publisher 在用户 Web 批准后原子校验 approval + payload + scope 才创建 valid anchor；Agent/浏览器绝不发布。",
      parameters: anchorActionParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("publish_illustration", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "attach_illustration_to_text",
      label: "Attach Illustration To Text (Proposal)",
      description:
        "Phase 34 action（REQ-VIS-05 / REQ-AGENT-03/04/07）：提议把锚点绑定到**精确文本跨度**（candidate-only）。服务端 proposal gate 只接受 proposal-ready + rights cleared 的 AssetRevision 与精确 source span/hash（D-34-01）；创建候选 IllustrationAnchorProposal + pending Web ApprovalRequest（action=attach_illustration_to_text，payload_hash 确定性重放）。绝不发布——确定性 publisher 在用户 Web 批准后原子校验 approval + payload + scope 才创建 valid anchor；Agent/浏览器绝不发布。",
      parameters: anchorActionParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("attach_illustration_to_text", params as unknown, signal, auth, runNovelId),
    }),
  ];
}

/** Phase 34 锚点提议 action 工具参数（镜像 backend schemas.agent_tools）。 */
function anchorActionParams() {
  return Type.Object({
    novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
    branch: Type.Optional(Type.String({ maxLength: 80, description: "衍生分支；原始主线为 null" })),
    fork: Type.Optional(Type.String({ maxLength: 80, description: "衍生 fork（仅 derivative mode）" })),
    chapter_id: Type.Integer({ minimum: 1, description: "锚点目标章节 ID" }),
    chapter_number: Type.Integer({ minimum: 1, description: "锚点目标章节号" }),
    proposal_key: Type.String({ minLength: 1, maxLength: 160, description: "幂等重放提案键（D-34-01）" }),
    source_snapshot_id: Type.String({ minLength: 1, maxLength: 160, description: "source snapshot 血缘 ID" }),
    source_snapshot_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "source snapshot 血缘 hash",
    }),
    source_start: Type.Integer({ minimum: 0, description: "精确 source span 起点（含）" }),
    source_end: Type.Integer({ minimum: 1, description: "精确 source span 终点（不含）" }),
    paragraph_start: Type.Optional(Type.Integer({ minimum: 1, description: "可选段落起点" })),
    paragraph_end: Type.Optional(Type.Integer({ minimum: 1, description: "可选段落终点" })),
    excerpt: Type.String({ minLength: 1, maxLength: 20000, description: "锚点覆盖的精确原文摘录" }),
    anchor_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "excerpt 的 SHA-256（D-34-01）",
    }),
    chapter_content_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "锚点冻结时的章节正文 SHA-256",
    }),
    asset_revision_id: Type.Integer({ minimum: 1, description: "proposal-ready AssetRevision ID（Phase 33 handoff）" }),
    caption: Type.String({ minLength: 1, maxLength: 500, description: "可访问 caption（D-34-02）" }),
    alt_text: Type.String({ minLength: 1, maxLength: 500, description: "可访问 alt 文本（D-34-02）" }),
    citation: Type.String({ minLength: 1, maxLength: 1000, description: "引用来源（D-34-02）" }),
  });
}
