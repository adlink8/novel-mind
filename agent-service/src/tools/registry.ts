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

/** 18 个域工具名（固定顺序；唯一 allowlist 事实源）。 */
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
  "get_visual_bible",
  "generate_image_candidate",
  "publish_illustration",
  "attach_illustration_to_text",
  "create_canon_fork",
  "apply_derivative_edit",
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
        fastapiToolCall("get_visual_bible", params as unknown, signal, auth),
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
        fastapiToolCall("generate_image_candidate", params as unknown, signal, auth),
    }),
    // ── Phase 34 锚点提议 action 工具（34-05）──
    defineTool({
      name: "publish_illustration",
      label: "Publish Illustration (Proposal)",
      description:
        "Phase 34 action（REQ-VIS-05 / REQ-AGENT-03/04/07）：提议发布**一个**锚点（candidate-only）。服务端 proposal gate 只接受 proposal-ready + rights cleared 的 AssetRevision（Phase 33 handoff）与精确 source span（excerpt + anchor_hash + chapter_content_hash + source snapshot，D-34-01）；创建候选 IllustrationAnchorProposal + pending Web ApprovalRequest（action=publish_illustration，payload_hash 确定性重放）。绝不发布——确定性 publisher 在用户 Web 批准后原子校验 approval + payload + scope 才创建 valid anchor；Agent/浏览器绝不发布。",
      parameters: anchorActionParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("publish_illustration", params as unknown, signal, auth),
    }),
    defineTool({
      name: "attach_illustration_to_text",
      label: "Attach Illustration To Text (Proposal)",
      description:
        "Phase 34 action（REQ-VIS-05 / REQ-AGENT-03/04/07）：提议把锚点绑定到**精确文本跨度**（candidate-only）。服务端 proposal gate 只接受 proposal-ready + rights cleared 的 AssetRevision 与精确 source span/hash（D-34-01）；创建候选 IllustrationAnchorProposal + pending Web ApprovalRequest（action=attach_illustration_to_text，payload_hash 确定性重放）。绝不发布——确定性 publisher 在用户 Web 批准后原子校验 approval + payload + scope 才创建 valid anchor；Agent/浏览器绝不发布。",
      parameters: anchorActionParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("attach_illustration_to_text", params as unknown, signal, auth),
    }),
    // ── Phase 35 canon fork 提议 action 工具（35-05）──
    defineTool({
      name: "create_canon_fork",
      label: "Create Canon Fork (Proposal)",
      description:
        "Phase 35 action（REQ-FORK-01 / REQ-AGENT-03/04/07）：提议**一个** canon fork（candidate-only）。服务端 proposal gate 只接受冻结 fork manifest（server-derived cutoff + 精确 source snapshot，D-35-03）+ delta 意图（delta_key + delta_content）；创建候选 CanonFork（status=candidate）+ pending Web ApprovalRequest（action=create_canon_fork，payload_hash 确定性重放）。绝不物化 fork——确定性 Fork materializer（app.services.canon_fork.materializer）在用户 Web 批准后原子校验 approval + payload + fork manifest + snapshot 重放 + delta 血缘 + owner/novel/branch/fork scope 才把 fork 物化为 approved；Original Canon 不可变、active pointer 恒 false。",
      parameters: canonForkParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("create_canon_fork", params as unknown, signal, auth),
    }),
    // ── Phase 36 derivative 编辑提议 action 工具（36-05）──
    defineTool({
      name: "apply_derivative_edit",
      label: "Apply Derivative Edit (Proposal)",
      description:
        "Phase 36 action（REQ-FORK-02 / REQ-AGENT-03/04/07）：提议**一个**派生 chapter patch（candidate-only）。服务端 proposal gate 只接受冻结 source snapshot 血缘 + 有效 project/chapter scope + base_revision CAS 锚（D-36-02）；创建候选 DerivativeEditProposal（proposal_status=proposed）+ pending Web ApprovalRequest（action=apply_derivative_edit，payload_hash 确定性重放）。绝不直接应用——确定性 Revision Service（app.services.derivative_editor.revisions.apply_agent_edit）在用户 Web 批准后原子校验 approval + payload + 冻结 proposal artifact 血缘 + owner/novel/branch/fork scope + 同一 base_revision CAS 才把 approved proposal 应用为 append-only agent_proposal 修订；Agent 绝不写 Original Canon / user autosave / published 状态。",
      parameters: derivativeEditParams(),
      execute: (toolCallId, params, signal) =>
        fastapiToolCall("apply_derivative_edit", params as unknown, signal, auth),
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
