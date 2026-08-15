/**
 * 域工具注册表（38-05 / 39-05 / REQ-FORK-04/05 / REQ-AGENT-03/04/07）——
 * derivative visual 发布与导出 action 域。
 *
 * 拆分自 ``tools/registry.ts``（registry 保留为 re-export 兼容层）：本模块持有
 * Phase 38 的 publish_derivative_visual 与 Phase 39 的 approve_export /
 * materialize_export 三个 action 工具与 3 个私有参数 helper
 * （publishDerivativeVisualParams / approveExportParams / materializeExportParams）。
 * 每个工具都是显式 ``defineTool``，参数用 TypeBox ``Type.Object`` 镜像后端请求
 * schema；``execute`` 转发到 FastAPI 门面（approval 绑定 + preparation_hash 重放
 * 服务端强制，fail closed）。
 */

import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fastapiToolCall } from "../fastapi-client.js";
import type { ToolAuth } from "../registry.js";

export function buildDerivativeExportTools(auth: ToolAuth, runNovelId: number) {
  return [
    // ── Phase 38 branch-aware derivative visual action 工具（38-05）──
    defineTool({
      name: "publish_derivative_visual",
      label: "Publish Derivative Visual (Proposal)",
      description:
        "Phase 38 action（REQ-FORK-04 / REQ-AGENT-03/04/07）：为已存储 derivative candidate asset 提议**一个**独立 publish ApprovalRequest（candidate-only）。服务端 action 只接受 owner/novel/fork scope 内可批准（candidate/needs_review）的候选，payload_hash 绑定候选冻结血缘（asset_id/content_hash/scene_spec_hash/divergence_manifest_hash/consistency_verdict/source_snapshot_hash/fork_id；blocked candidate / wrong owner/branch/fork → fail closed）。绝不发布——只有独立 publish_derivative_visual approval 被用户批准后，确定性 review seam（review_candidate_asset）才能把 candidate 物化为 approved published asset；Agent 绝不写 Original Canon / Visual Bible / domain 表 / published 状态。",
      parameters: publishDerivativeVisualParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("publish_derivative_visual", params as unknown, signal, auth, runNovelId),
    }),
    // ── Phase 39 derivative export action 工具（39-05）──
    defineTool({
      name: "approve_export",
      label: "Approve Export (Proposal)",
      description:
        "Phase 39 action（REQ-FORK-05 / REQ-AGENT-03/04/07）：为已 finalize 候选 ExportPreparationArtifact 提议**一个**独立 approve_export ApprovalRequest（candidate-only）。服务端 action 只接受 owner/novel/branch/fork/project scope 内的 candidate artifact，payload_hash 绑定 artifact revision + 确定性 preparation_hash（服务端重放冻结 manifest；wrong owner/branch/fork/stale hash → fail closed）。绝不物化——只有独立 approve_export approval 被用户批准后，确定性 materializer（materialize_export）才能把候选 artifact 推进为 approved 并产出可复现 bundle；Agent 绝不写 Original Canon / 域表 / Artifact 状态 / bundle。",
      parameters: approveExportParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("approve_export", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "materialize_export",
      label: "Materialize Export (Approved Only)",
      description:
        "Phase 39 action（REQ-FORK-05 / REQ-AGENT-03/04/07）：确定性 materializer 消费一个已批准的 approve_export ApprovalRequest（candidate-only 边界）。只接受 owner/novel/branch/fork/project scope 内已 finalize 的候选 ExportPreparationArtifact + preparation_hash 匹配的 approve_export approval；服务端原子校验 approval action + 相同 preparation_hash 绑定 + artifact revision 血缘 + 冻结 manifest 重放，才把候选 artifact 推进为 approved 并产出可复现 bundle（Markdown/EPUB/package 由 frozen manifest 复算）。download 只读、永不改变 Artifact status / approval lineage。forged/expired/cancelled/rejected approval、stale hash、wrong scope、pending/rejected artifact → fail closed，无 bundle 或权威写入。",
      parameters: materializeExportParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("materialize_export", params as unknown, signal, auth, runNovelId),
    }),
  ];
}

/** Phase 38 publish_derivative_visual action 工具参数（镜像 backend schemas.agent_tools）。 */
function publishDerivativeVisualParams() {
  return Type.Object({
    novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
    branch: Type.Optional(Type.String({ maxLength: 80, description: "衍生分支；原始主线为 null" })),
    fork: Type.Optional(Type.String({ maxLength: 80, description: "衍生 fork（仅 derivative mode）" })),
    candidate_asset_id: Type.Integer({ minimum: 1, description: "已存储 derivative candidate asset ID（服务端重验 owner/novel/fork 血缘 + approvable review_state）" }),
    scene_spec_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "frozen canonical derivative Scene Spec 血缘 hash（服务端与 candidate 血缘重放；drift → fail closed）",
    }),
    approval_note: Type.Optional(Type.String({ maxLength: 4000, description: "供发布 approval 展示的显式批准备注" })),
    run_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillRun ID 血缘" })),
    skill_version_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillVersion ID 血缘" })),
    artifact_id: Type.Optional(Type.Integer({ minimum: 1, description: "Artifact ID 血缘" })),
    artifact_revision_id: Type.Optional(Type.Integer({ minimum: 1, description: "ArtifactRevision ID 血缘" })),
  });
}

/** Phase 39 approve_export action 工具参数（镜像 backend schemas.agent_tools）。 */
function approveExportParams() {
  return Type.Object({
    novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
    branch: Type.Optional(Type.String({ maxLength: 80, description: "衍生分支；原始主线为 null" })),
    fork: Type.Optional(Type.String({ maxLength: 80, description: "衍生 fork（仅 derivative mode）" })),
    project_id: Type.Integer({ minimum: 1, description: "derivative project ID（服务端重验 owner/novel + fanfiction_canon 空间）" }),
    artifact_id: Type.Integer({ minimum: 1, description: "候选 ExportPreparationArtifact ID（服务端重验 owner/novel + candidate status）" }),
    artifact_revision_id: Type.Integer({ minimum: 1, description: "候选 ArtifactRevision ID（approval payload 绑定）" }),
    preparation_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "候选冻结 preparation hash（服务端从 artifact revision + 冻结 manifest 重放；stale → fail closed）",
    }),
    approval_note: Type.Optional(Type.String({ maxLength: 4000, description: "供 approval 展示的显式批准备注" })),
    run_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillRun ID 血缘" })),
    skill_version_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillVersion ID 血缘" })),
  });
}

/** Phase 39 materialize_export action 工具参数（镜像 backend schemas.agent_tools）。 */
function materializeExportParams() {
  return Type.Object({
    novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
    branch: Type.Optional(Type.String({ maxLength: 80, description: "衍生分支；原始主线为 null" })),
    fork: Type.Optional(Type.String({ maxLength: 80, description: "衍生 fork（仅 derivative mode）" })),
    project_id: Type.Integer({ minimum: 1, description: "derivative project ID（服务端重验 owner/novel + fanfiction_canon 空间）" }),
    artifact_id: Type.Integer({ minimum: 1, description: "候选 ExportPreparationArtifact ID（只接受 approved artifact）" }),
    artifact_revision_id: Type.Integer({ minimum: 1, description: "候选 ArtifactRevision ID（approval payload 绑定）" }),
    approval_id: Type.Integer({ minimum: 1, description: "已批准的 approve_export ApprovalRequest ID（服务端重验 action + status + preparation_hash）" }),
    preparation_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "候选冻结 preparation hash（必须与 approve_export approval payload_hash 一致）",
    }),
    reason: Type.Optional(Type.String({ maxLength: 4000, description: "确定性 materialize 理由（展示/审计）" })),
    run_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillRun ID 血缘" })),
    skill_version_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillVersion ID 血缘" })),
  });
}
