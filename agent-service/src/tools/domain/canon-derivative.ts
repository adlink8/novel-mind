/**
 * 域工具注册表（35-05 / 36-05 / 37-05 / REQ-FORK-01..03 / REQ-AGENT-03/04/07 /
 * D-35-03 / D-36-02）——canon fork 与 derivative 提议 action 域。
 *
 * 拆分自 ``tools/registry.ts``（registry 保留为 re-export 兼容层）：本模块持有
 * Phase 35-37 的 4 个提议 action 工具（create_canon_fork / apply_derivative_edit /
 * allow_divergence / publish_derivative_revision）与 4 个私有参数 helper
 * （canonForkParams / derivativeEditParams / allowDivergenceParams /
 * publishDerivativeRevisionParams）。每个工具都是显式 ``defineTool``，参数用
 * TypeBox ``Type.Object`` 镜像后端请求 schema；``execute`` 转发到 FastAPI 门面
 * （proposal gate / payload_hash 确定性重放服务端强制，D-35-03/D-36-02）。
 */

import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fastapiToolCall } from "../fastapi-client.js";
import type { ToolAuth } from "../registry.js";

export function buildCanonDerivativeTools(auth: ToolAuth, runNovelId: number) {
  return [
    // ── Phase 35 canon fork 提议 action 工具（35-05）──
    defineTool({
      name: "create_canon_fork",
      label: "Create Canon Fork (Proposal)",
      description:
        "Phase 35 action（REQ-FORK-01 / REQ-AGENT-03/04/07）：提议**一个** canon fork（candidate-only）。服务端 proposal gate 只接受冻结 fork manifest（server-derived cutoff + 精确 source snapshot，D-35-03）+ delta 意图（delta_key + delta_content）；创建候选 CanonFork（status=candidate）+ pending Web ApprovalRequest（action=create_canon_fork，payload_hash 确定性重放）。绝不物化 fork——确定性 Fork materializer（app.services.canon_fork.materializer）在用户 Web 批准后原子校验 approval + payload + fork manifest + snapshot 重放 + delta 血缘 + owner/novel/branch/fork scope 才把 fork 物化为 approved；Original Canon 不可变、active pointer 恒 false。",
      parameters: canonForkParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("create_canon_fork", params as unknown, signal, auth, runNovelId),
    }),
    // ── Phase 36 derivative 编辑提议 action 工具（36-05）──
    defineTool({
      name: "apply_derivative_edit",
      label: "Apply Derivative Edit (Proposal)",
      description:
        "Phase 36 action（REQ-FORK-02 / REQ-AGENT-03/04/07）：提议**一个**派生 chapter patch（candidate-only）。服务端 proposal gate 只接受冻结 source snapshot 血缘 + 有效 project/chapter scope + base_revision CAS 锚（D-36-02）；创建候选 DerivativeEditProposal（proposal_status=proposed）+ pending Web ApprovalRequest（action=apply_derivative_edit，payload_hash 确定性重放）。绝不直接应用——确定性 Revision Service（app.services.derivative_editor.revisions.apply_agent_edit）在用户 Web 批准后原子校验 approval + payload + 冻结 proposal artifact 血缘 + owner/novel/branch/fork scope + 同一 base_revision CAS 才把 approved proposal 应用为 append-only agent_proposal 修订；Agent 绝不写 Original Canon / user autosave / published 状态。",
      parameters: derivativeEditParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("apply_derivative_edit", params as unknown, signal, auth, runNovelId),
    }),
    // ── Phase 37 derivative generation action 工具（37-05）──
    defineTool({
      name: "allow_divergence",
      label: "Allow Divergence (Proposal)",
      description:
        "Phase 37 action（REQ-FORK-03 / REQ-AGENT-03/04/07）：为 blocked / needs_override 生成候选提议**一个**显式 divergence override（candidate-only）。服务端 override gate 只接受理由 + 受影响 leaf 证据（或候选已声明的 CanonDelta），并校验 draft_hash / canon_delta_hash 从候选确定性血缘重放（drift → fail closed）；创建 pending DerivativeOverride + pending Web ApprovalRequest（action=allow_divergence，payload_hash 绑定 exact draft_hash + canon_delta_hash）。绝不发布——只有先确认本 approval 再经独立 publish_derivative_revision approval 后由确定性 revision publisher 物化；Agent 绝不写 Original Canon / 域表 / published 状态。",
      parameters: allowDivergenceParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("allow_divergence", params as unknown, signal, auth, runNovelId),
    }),
    defineTool({
      name: "publish_derivative_revision",
      label: "Publish Derivative Revision (Proposal)",
      description:
        "Phase 37 action（REQ-FORK-03 / REQ-AGENT-03/04/07）：只在 allow_divergence approval 已批准 + 完整 revalidation 通过后为同一候选提议**一个**独立 publish ApprovalRequest（candidate-only）。服务端绑定与 allow_divergence approval **完全相同**的 draft_hash + canon_delta_hash（相同 hash 绑定；跳过/漂移 → fail closed），绝不复用 allow_divergence approval——只有独立 publish approval 被用户批准后，确定性 revision publisher（consume_publish_approval）才能物化 Fanfiction Canon 修订；Agent 绝不写 Original Canon / 域表 / published 状态。",
      parameters: publishDerivativeRevisionParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("publish_derivative_revision", params as unknown, signal, auth, runNovelId),
    }),
  ];
}

/** Phase 35 canon fork 提议 action 工具参数（镜像 backend schemas.agent_tools）。 */
function canonForkParams() {
  return Type.Object({
    novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
    branch: Type.Optional(Type.String({ maxLength: 80, description: "衍生分支；原始主线为 null" })),
    fork: Type.Optional(Type.String({ maxLength: 80, description: "衍生 fork（仅 derivative mode）" })),
    fork_key: Type.String({ minLength: 1, maxLength: 128, description: "幂等 fork 标识（owner/novel 范围内唯一且不可变，D-35-03）" }),
    requested_cutoff_chapter: Type.Optional(
      Type.Integer({ minimum: 1, description: "请求的 spoiler cutoff 章节（最终 cutoff 服务端派生）" }),
    ),
    full_book_requested: Type.Optional(
      Type.Boolean({ description: "请求全本 cutoff（无显式服务端授权时 fail closed）" }),
    ),
    expected_source_snapshot_hash: Type.Optional(
      Type.String({
        minLength: 64,
        maxLength: 64,
        pattern: "^[0-9a-f]{64}$",
        description: "预期的 source snapshot 血缘 hash（服务端重放；stale → 409）",
      }),
    ),
    delta_key: Type.String({ minLength: 1, maxLength: 160, description: "幂等 delta 提案键（approval payload 绑定）" }),
    delta_content: Type.String({ minLength: 1, maxLength: 50000, description: "候选 derivative 内容（服务端计算 content_hash 并绑定 approval payload）" }),
    delta_evidence_refs: Type.Array(
      Type.String({ minLength: 1, maxLength: 64 }),
      { minItems: 1, description: "delta 引用的 leaf 证据键（必须属于冻结 citation lineage 白名单）" },
    ),
    run_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillRun ID 血缘" })),
    skill_version_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillVersion ID 血缘" })),
    artifact_id: Type.Optional(Type.Integer({ minimum: 1, description: "Artifact ID 血缘" })),
    artifact_revision_id: Type.Optional(Type.Integer({ minimum: 1, description: "ArtifactRevision ID 血缘" })),
  });
}

/** Phase 36 derivative 编辑提议 action 工具参数（镜像 backend schemas.agent_tools）。 */
function derivativeEditParams() {
  return Type.Object({
    novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
    branch: Type.Optional(Type.String({ maxLength: 80, description: "衍生分支；原始主线为 null" })),
    fork: Type.Optional(Type.String({ maxLength: 80, description: "衍生 fork（仅 derivative mode）" })),
    project_id: Type.Integer({ minimum: 1, description: "derivative project ID（服务端重验 owner/novel + fanfiction_canon 空间）" }),
    chapter_id: Type.Integer({ minimum: 1, description: "派生 chapter ID（服务端重验 project 范围）" }),
    chapter_number: Type.Integer({ minimum: 1, description: "派生 chapter 序号" }),
    proposal_key: Type.String({ minLength: 1, maxLength: 160, description: "幂等 proposal 键（approval payload 绑定）" }),
    base_revision: Type.Integer({ minimum: 1, description: "chapter 乐观并发 token（同一 base_revision CAS，D-36-02）" }),
    content: Type.String({ minLength: 1, maxLength: 50000, description: "候选 Markdown patch（服务端计算 content_hash 并绑定 approval payload）" }),
    source_snapshot_id: Type.Optional(Type.String({ maxLength: 160, description: "source snapshot 血缘 ID" })),
    source_snapshot_hash: Type.Optional(
      Type.String({
        minLength: 64,
        maxLength: 64,
        pattern: "^[0-9a-f]{64}$",
        description: "source snapshot 血缘 hash（服务端与 project 冻结 fork 血缘重放；drift → fail closed）",
      }),
    ),
    evidence_refs: Type.Array(
      Type.String({ minLength: 1, maxLength: 64 }),
      { minItems: 1, description: "proposal 引用的 leaf 证据键（必须属于冻结 manifest 白名单）" },
    ),
    run_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillRun ID 血缘" })),
    skill_version_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillVersion ID 血缘" })),
    artifact_id: Type.Optional(Type.Integer({ minimum: 1, description: "Artifact ID 血缘" })),
    artifact_revision_id: Type.Optional(Type.Integer({ minimum: 1, description: "ArtifactRevision ID 血缘" })),
  });
}

/** Phase 37 allow_divergence action 工具参数（镜像 backend schemas.agent_tools）。 */
function allowDivergenceParams() {
  return Type.Object({
    novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
    branch: Type.Optional(Type.String({ maxLength: 80, description: "衍生分支；原始主线为 null" })),
    fork: Type.Optional(Type.String({ maxLength: 80, description: "衍生 fork（仅 derivative mode）" })),
    project_id: Type.Integer({ minimum: 1, description: "derivative project ID（服务端重验 owner/novel + fanfiction_canon 空间）" }),
    chapter_id: Type.Integer({ minimum: 1, description: "派生 chapter ID（服务端重验 project 范围）" }),
    candidate_id: Type.Integer({ minimum: 1, description: "generation candidate ID（服务端重验 owner/novel 血缘 + overridable verdict）" }),
    reason: Type.String({ minLength: 1, maxLength: 4000, description: "显式 divergence 理由（空 → fail closed）" }),
    affected_evidence: Type.Array(
      Type.String({ minLength: 1, maxLength: 64 }),
      { description: "受影响的 leaf 证据键（必须 ⊆ 冻结 package 白名单）" },
    ),
    kind: Type.Optional(Type.String({ maxLength: 32, description: "可选 CanonDelta 类型（候选未声明时必填）" })),
    draft_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "候选结构化输出的 canonical draft hash（服务端重放；drift → fail closed）",
    }),
    canon_delta_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "候选 CanonDelta hash（服务端重放；与 approval payload 绑定）",
    }),
    run_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillRun ID 血缘" })),
    skill_version_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillVersion ID 血缘" })),
    artifact_id: Type.Optional(Type.Integer({ minimum: 1, description: "Artifact ID 血缘" })),
    artifact_revision_id: Type.Optional(Type.Integer({ minimum: 1, description: "ArtifactRevision ID 血缘" })),
  });
}

/** Phase 37 publish_derivative_revision action 工具参数（镜像 backend schemas.agent_tools）。 */
function publishDerivativeRevisionParams() {
  return Type.Object({
    novel_id: Type.Integer({ minimum: 1, description: "小说 ID" }),
    branch: Type.Optional(Type.String({ maxLength: 80, description: "衍生分支；原始主线为 null" })),
    fork: Type.Optional(Type.String({ maxLength: 80, description: "衍生 fork（仅 derivative mode）" })),
    override_id: Type.Integer({ minimum: 1, description: "已存在的 pending DerivativeOverride ID" }),
    draft_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "候选 canonical draft hash（必须与 allow_divergence approval 完全一致）",
    }),
    canon_delta_hash: Type.String({
      minLength: 64,
      maxLength: 64,
      pattern: "^[0-9a-f]{64}$",
      description: "候选 CanonDelta hash（必须与 allow_divergence approval 完全一致）",
    }),
    approval_note: Type.Optional(Type.String({ maxLength: 4000, description: "供发布 approval 展示的显式批准备注" })),
    run_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillRun ID 血缘" })),
    skill_version_id: Type.Optional(Type.Integer({ minimum: 1, description: "SkillVersion ID 血缘" })),
    artifact_id: Type.Optional(Type.Integer({ minimum: 1, description: "Artifact ID 血缘" })),
    artifact_revision_id: Type.Optional(Type.Integer({ minimum: 1, description: "ArtifactRevision ID 血缘" })),
  });
}
