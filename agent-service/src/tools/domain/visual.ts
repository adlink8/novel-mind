/**
 * 域工具注册表（31-04 / 33-05）——Visual Bible 只读 + 候选生成域。
 *
 * 拆分自 ``tools/registry.ts``（registry 保留为 re-export 兼容层）：本模块持有
 * Phase 30/31 的 get_visual_bible（candidate-only 只读）与 Phase 33 的
 * generate_image_candidate（候选生成 action）。每个工具都是显式 ``defineTool``，
 * 参数用 TypeBox ``Type.Object`` 镜像后端请求 schema；``execute`` 转发到
 * FastAPI 门面（generation gate / 幂等血缘服务端强制，D-33-01）。
 */

import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fastapiToolCall } from "../fastapi-client.js";
import type { ToolAuth } from "../registry.js";

export function buildVisualTools(auth: ToolAuth, runNovelId: number) {
  return [
    // ── Phase 30 Visual Bible 只读工具（31-04）──
    defineTool({
      name: "get_visual_bible",
      label: "Get Visual Bible Version",
      description:
        "Visual Bible 候选版本视图（Phase 30/31）。owner/novel 范围服务端强制；candidate-only 只读——approval 权威只在 FastAPI review API（D-30-04），本工具绝不批准/发布任何版本。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        version_id: Type.Optional(
          Type.Integer({ minimum: 1, description: "Visual Bible 版本 ID（缺省列表）" }),
        ),
        approved_only: Type.Optional(
          Type.Boolean({ description: "只返回 review_state=approved 的候选版本" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("get_visual_bible", params as unknown, signal, auth, runNovelId),
    }),
    // ── Phase 33 候选生成 action 工具（33-05）──
    defineTool({
      name: "generate_image_candidate",
      label: "Generate Image Candidate",
      description:
        "Phase 33 候选生成 action（REQ-VIS-04 / REQ-AGENT-02/03/04）：创建**一个**候选生成作业。服务端 generation gate 只接受已批准且非 stale 的 PromptRevision（D-33-01）；作业 idempotency key 从 owner/novel/SceneSpec/prompt/model/config 血缘确定性重放。绝不写 Canon / 域表 / ApprovalRequest / published 状态——审批与发布属于 Phase 34；候选资产由 durable worker 在作业成功时产出。",
      parameters: Type.Object({
        novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
        prompt_revision_id: Type.Integer({
          minimum: 1,
          description: "已批准 PromptRevision ID（服务端重验 approved + 非 stale）",
        }),
        job_key: Type.String({ minLength: 1, description: "幂等重放作业键" }),
        provider: Type.Optional(
          Type.String({ minLength: 1, description: "提供商（当前仅 mock 配置）" }),
        ),
        model: Type.Optional(
          Type.String({ minLength: 1, description: "生成模型" }),
        ),
        width: Type.Optional(
          Type.Integer({ minimum: 16, maximum: 4096, description: "图像宽度" }),
        ),
        height: Type.Optional(
          Type.Integer({ minimum: 16, maximum: 4096, description: "图像高度" }),
        ),
      }),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("generate_image_candidate", params as unknown, signal, auth, runNovelId),
    }),
  ];
}
