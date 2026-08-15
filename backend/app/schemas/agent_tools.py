"""
智能体工具门面请求/响应 Pydantic 模型（25.2-02 Domain Tool Contract）。

设计约定:
  - 所有请求模型使用 StrictAgentToolModel（extra="forbid"），未知字段直接
    422，避免 agent（可能被提示注入）悄悄塞入服务端未声明的参数。
  - novel_id **不放在请求体**中，而是通过查询参数注入
    ``Depends(require_owned_novel)``，使 owner 校验与 404-hide 在路由签名上
    结构上不可避免。
  - ``full_book`` 字段被有意省略：服务端只读取持久化的每本小说开关
    （novel.reading_progress["timeline_full_book"]），绝不接受裸请求参数
    （D-07 防剧透）。
  - 响应模型：成功时返回各领域既有响应形状（NovelResponse / ChapterResponse /
    SearchResponse / TimelineEnvelope / RelationshipGraphEnvelope / 线索信封 /
    NM 信封）；失败时统一返回 AgentToolErrorEnvelope。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictAgentToolModel(BaseModel):
    """严格输入模型：未知字段一律拒绝（fail closed）。"""

    model_config = ConfigDict(extra="forbid")


# ────────────────────────── 各工具请求模型 ──────────────────────────


class GetNovelRequest(StrictAgentToolModel):
    """获取小说元信息。novel_id 由查询参数注入。"""


class GetChapterRequest(StrictAgentToolModel):
    """获取章节全文（受 per-tool 字节上限与 spoiler cutoff 约束）。"""

    chapter_id: int = Field(..., gt=0, description="章节 ID")


class SearchNovelTextRequest(StrictAgentToolModel):
    """小说内全文检索（raw chunks + NU，见 ADR-0002）。"""

    query: str = Field(..., min_length=1, max_length=500, description="检索查询文本")
    top_k: int = Field(default=10, ge=1, le=50, description="返回结果数量上限")
    mode: Literal["auto", "chunks", "units", "hybrid"] = Field(
        default="auto",
        description="检索意图；实际执行层由服务端 router 决策并诚实标注",
    )


class GetTimelineRequest(StrictAgentToolModel):
    """时间线事件（spoiler cutoff 由服务端 resolve_chapter_cutoff 决定）。"""

    ordering: Literal["narrative", "story"] = Field(
        default="narrative", description="排序方式"
    )
    person: str | None = Field(default=None, max_length=100, description="人物过滤")
    causal: bool = Field(default=False, description="是否包含因果边")
    chapter_start: int | None = Field(default=None, ge=1, description="起始章节（含）")
    chapter_end: int | None = Field(
        default=None, ge=1, description="结束章节（含）；超过服务端截止点将被拒绝"
    )


class GetRelationshipsRequest(StrictAgentToolModel):
    """人物关系图（spoiler cutoff 由服务端决定）。"""

    source: Literal["active", "running_candidate"] = Field(
        default="active", description="关系版本来源"
    )
    version_id: int | None = Field(default=None, gt=0, description="显式版本 ID")
    through_chapter: int | None = Field(
        default=None, gt=0, description="截止章节；超过服务端截止点将被拒绝"
    )
    character_id: int | None = Field(default=None, gt=0, description="人物过滤")
    relation_type: str | None = Field(
        default=None, max_length=32, description="关系类型过滤"
    )
    include_provisional: bool = Field(default=False, description="是否包含临时共现边")


class GetCluesRequest(StrictAgentToolModel):
    """线索与伏笔信封（spoiler cutoff 由服务端决定）。"""

    character_id: int | None = Field(default=None, gt=0, description="人物过滤")
    status: str | None = Field(default=None, max_length=32, description="状态过滤")


class GetNarrativeMemoryRequest(StrictAgentToolModel):
    """叙事记忆结构（候选-only，ADR-0002，响应带 release_status="candidate"）。"""

    version_id: int | None = Field(default=None, gt=0, description="显式版本 ID")
    view: Literal["versions", "tree"] = Field(
        default="versions",
        description="versions=版本列表；tree=指定版本的结构树",
    )
    through_chapter: int | None = Field(
        default=None, gt=0, description="截止章节（tree 视图可见性过滤）"
    )


# ────────────────────────── Phase 27 世界模型工具请求模型（27-05） ──────────────────────────


class GetEventsRequest(StrictAgentToolModel):
    """世界模型事件/因果投影（REQ-WM-01，D-05 cutoff 服务端强制）。

    version_id 缺省取该 owner/novel 最新版本；cutoff 显式提供时超过服务端
    截止点被拒绝（beyond_cutoff）。返回候选投影（含证据、lineage、conflicts）。
    """

    version_id: int | None = Field(default=None, gt=0, description="世界模型版本 ID")
    cutoff: int | None = Field(default=None, ge=1, description="截止章节（D-05）")


class GetCharacterStateRequest(StrictAgentToolModel):
    """角色状态/目标/动机（REQ-WM-02，D-05 cutoff/POV 服务端强制）。

    只返回 aspect ∈ {state, goal, motivation} 的声明；hidden knowledge 在
    disclosure_cutoff 之前绝不下发（D-05）。无可见声明时 abstained，绝不编造。
    """

    subject: str = Field(..., min_length=1, max_length=100, description="角色名")
    version_id: int | None = Field(default=None, gt=0, description="世界模型版本 ID")
    cutoff: int | None = Field(default=None, ge=1, description="截止章节（D-05）")
    pov: str | None = Field(default=None, max_length=100, description="视角过滤（POV）")


class GetCharacterKnowledgeRequest(StrictAgentToolModel):
    """角色知识（REQ-WM-02，D-05 cutoff/POV 服务端强制）。

    只返回 aspect=knowledge 的声明；mistaken belief / hidden knowledge 保持显式
    标签，绝不静默升级为事实。
    """

    subject: str = Field(..., min_length=1, max_length=100, description="角色名")
    version_id: int | None = Field(default=None, gt=0, description="世界模型版本 ID")
    cutoff: int | None = Field(default=None, ge=1, description="截止章节（D-05）")
    pov: str | None = Field(default=None, max_length=100, description="视角过滤（POV）")


class GetWorldRulesRequest(StrictAgentToolModel):
    """世界规则与规则例外（REQ-WM-03，D-05 cutoff 服务端强制）。

    规则例外是 first-class 记录，绝不折叠进规则语句（D-04）。
    """

    version_id: int | None = Field(default=None, gt=0, description="世界模型版本 ID")
    cutoff: int | None = Field(default=None, ge=1, description="截止章节（D-05）")


class GetEvidenceSpanRequest(StrictAgentToolModel):
    """leaf 证据跨度（D-07/D-08）：按 chapter+offsets 物化原文。

    只返回冻结原文切片；offsets 非法 → 拒绝。``content_hash`` 可选：省略时
    服务端计算并返回；提供时校验与切片一致，不匹配 → 拒绝（防漂移）。

    ``chunk_id`` 通道：携带 search_novel_text 命中行的 chunk_id 时，服务端
    在章节原文中定位 chunk 内容并确定性推导 offsets，source_start/source_end
    可省略（模型无需自行数字符）；未携带 chunk_id 时 offsets 必填。
    """

    chapter_id: int = Field(..., gt=0, description="章节 ID")
    source_start: int | None = Field(
        default=None, ge=0, description="切片起点（含，code-point；chunk_id 缺省时必填）"
    )
    source_end: int | None = Field(
        default=None, gt=0, description="切片终点（不含；chunk_id 缺省时必填）"
    )
    chunk_id: int | None = Field(
        default=None,
        gt=0,
        description="text_chunks 行 ID（来自 search_novel_text 命中行）；提供时服务端推导 offsets",
    )
    content_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="该切片内容的 SHA-256（可选；省略时服务端计算）",
    )

    @model_validator(mode="after")
    def validate_offsets_or_chunk(self) -> "GetEvidenceSpanRequest":
        if self.chunk_id is None and (
            self.source_start is None or self.source_end is None
        ):
            raise ValueError("未携带 chunk_id 时 source_start/source_end 必填")
        return self


class GetVisualBibleRequest(StrictAgentToolModel):
    """Visual Bible 候选版本视图（31-04，Phase 30 Visual Bible 只读）。

    显式 ``version_id`` → 单个候选信封；缺省 → 版本列表。
    ``approved_only=True`` 只保留 review_state=approved 的版本（D-30-04）；
    approval 权威仍只在 FastAPI review API，本工具只读。
    """

    version_id: int | None = Field(
        default=None, gt=0, description="Visual Bible 版本 ID"
    )
    approved_only: bool = Field(
        default=False,
        description="只返回已批准（review_state=approved）的候选版本",
    )


class GenerateImageCandidateRequest(StrictAgentToolModel):
    """Phase 33 候选生成 action（33-05，REQ-VIS-04 / REQ-AGENT-02/03/04）。

    只创建**一个候选生成作业**（D-33-01..D-33-03）：服务端 generation gate 只
    接受已批准且非 stale 的 PromptRevision；作业 idempotency key 从
    owner/novel/SceneSpec/prompt/model/config 血缘确定性重放。novel_id 由查询
    参数注入（require_owned_novel），绝不放进请求体。本工具绝不写 Canon /
    域表 / ApprovalRequest / published 状态——审批与发布属于 Phase 34。
    """

    prompt_revision_id: int = Field(
        gt=0, description="已批准 PromptRevision ID（服务端重验 approved + 非 stale）"
    )
    job_key: str = Field(min_length=1, max_length=120, description="幂等重放作业键")
    provider: str = Field(
        default="mock",
        min_length=1,
        max_length=64,
        description="提供商（当前仅 mock 配置）",
    )
    model: str = Field(
        default="mock-img-v1", min_length=1, max_length=120, description="生成模型"
    )
    width: int = Field(default=1024, ge=16, le=4096, description="图像宽度")
    height: int = Field(default=1024, ge=16, le=4096, description="图像高度")


# ────────────────────────── Phase 34 锚点提议 action 工具（34-05） ──────────────────────────


class AnchorProposalActionRequest(StrictAgentToolModel):
    """Phase 34 锚点提议 action 请求（34-05，REQ-VIS-05 / REQ-AGENT-03/04/07）。

    ``publish_illustration`` / ``attach_illustration_to_text`` 共用同一请求形状：
    精确 source span（excerpt + anchor_hash + chapter_content_hash + source
    snapshot）+ proposal-ready AssetRevision（Phase 33 handoff）。novel_id 由查询
    参数注入（require_owned_novel），绝不放进请求体。服务端 proposal gate 只
    接受 proposal-ready + rights cleared 的 AssetRevision 与精确 hash/range
    （D-34-01）；创建候选 IllustrationAnchorProposal + pending Web
    ApprovalRequest（action + payload_hash 确定性重放，D-11/D-15）。绝不发布——
    确定性 publisher 在用户 Web 批准后原子校验 approval + payload + scope 才
    创建 valid anchor。
    """

    branch: str | None = Field(
        default=None, max_length=80, description="衍生分支；原始主线为 null"
    )
    fork: str | None = Field(
        default=None,
        max_length=80,
        description="衍生 fork（仅 derivative mode；original 必须为 null）",
    )
    chapter_id: int = Field(
        gt=0, description="锚点目标章节 ID（服务端重验 owner/novel 血缘）"
    )
    chapter_number: int = Field(ge=1, description="锚点目标章节号")
    proposal_key: str = Field(
        min_length=1, max_length=160, description="幂等重放提案键（D-34-01）"
    )
    source_snapshot_id: str = Field(
        min_length=1, max_length=160, description="source snapshot 血缘 ID"
    )
    source_snapshot_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="source snapshot 血缘 hash",
    )
    source_start: int = Field(
        ge=0, description="精确 source span 起点（含，code-point）"
    )
    source_end: int = Field(gt=0, description="精确 source span 终点（不含）")
    paragraph_start: int | None = Field(
        default=None, ge=1, description="可选段落起点（reader/export 布局坐标）"
    )
    paragraph_end: int | None = Field(
        default=None, ge=1, description="可选段落终点（reader/export 布局坐标）"
    )
    excerpt: str = Field(
        min_length=1, max_length=20000, description="锚点覆盖的精确原文摘录"
    )
    anchor_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="excerpt 的 SHA-256（D-34-01）；偏移/hash 不匹配即 stale，绝不静默移位",
    )
    chapter_content_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="锚点冻结时的章节正文 SHA-256",
    )
    asset_revision_id: int = Field(
        gt=0,
        description="proposal-ready AssetRevision ID（Phase 33 handoff；服务端重验 proposal_ready + rights cleared）",
    )
    caption: str = Field(
        min_length=1, max_length=500, description="可访问 caption（D-34-02）"
    )
    alt_text: str = Field(
        min_length=1, max_length=500, description="可访问 alt 文本（D-34-02）"
    )
    citation: str = Field(
        min_length=1, max_length=1000, description="引用来源（D-34-02）"
    )
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill/artifact 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(
        default=None, gt=0, description="SkillVersion ID 血缘"
    )
    artifact_id: int | None = Field(default=None, gt=0, description="Artifact ID 血缘")
    artifact_revision_id: int | None = Field(
        default=None, gt=0, description="ArtifactRevision ID 血缘"
    )


# ────────────────────────── Phase 35 canon fork 提议 action 工具（35-05） ──────────────────────────


class CreateCanonForkRequest(StrictAgentToolModel):
    """Phase 35 canon fork 提议 action 请求（35-05，REQ-FORK-01 / REQ-AGENT-03/04/07）。

    只创建**一个候选 fork**（D-35-03）：服务端 proposal gate 只接受冻结 fork
    manifest（server-derived cutoff + 精确 source snapshot）+ delta 意图
    （delta_key + delta_content）；创建候选 CanonFork（status=candidate）+
    pending Web ApprovalRequest（action=create_canon_fork，payload_hash 确定性
    重放，D-11/D-15）。novel_id 由查询参数注入（require_owned_novel），绝不放进
    请求体。绝不物化 fork——确定性 Fork materializer 在用户 Web 批准后原子校验
    approval + payload + fork manifest + snapshot 重放 + delta 血缘 +
    owner/novel/branch/fork scope 才把 fork 物化为 approved；Original Canon
    不可变、active pointer 恒 false。
    """

    branch: str | None = Field(
        default=None, max_length=80, description="衍生分支；原始主线为 null"
    )
    fork: str | None = Field(
        default=None,
        max_length=80,
        description="衍生 fork（仅 derivative mode；original 必须为 null）",
    )
    fork_key: str = Field(
        min_length=1,
        max_length=128,
        description="幂等 fork 标识（owner/novel 范围内唯一且不可变，D-35-03）",
    )
    requested_cutoff_chapter: int | None = Field(
        default=None,
        ge=1,
        description="请求的 spoiler cutoff 章节（最终 cutoff 由服务端派生）",
    )
    full_book_requested: bool = Field(
        default=False,
        description="请求全本 cutoff（无显式服务端授权时 fail closed，403）",
    )
    expected_source_snapshot_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="预期的 source snapshot 血缘 hash（服务端重放；stale → 409 拒绝）",
    )
    delta_key: str = Field(
        min_length=1,
        max_length=160,
        description="幂等 delta 提案键（approval payload 绑定）",
    )
    delta_content: str = Field(
        min_length=1,
        max_length=50000,
        description="候选 derivative 内容（服务端计算 content_hash 并绑定 approval payload）",
    )
    delta_evidence_refs: list[str] = Field(
        min_length=1,
        description="delta 引用的 leaf 证据键（必须属于冻结 citation lineage 白名单）",
    )
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill/artifact 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(
        default=None, gt=0, description="SkillVersion ID 血缘"
    )
    artifact_id: int | None = Field(default=None, gt=0, description="Artifact ID 血缘")
    artifact_revision_id: int | None = Field(
        default=None, gt=0, description="ArtifactRevision ID 血缘"
    )


# ────────────────────────── Phase 36 derivative 编辑提议 action 工具（36-05） ──────────────────────────


class ApplyDerivativeEditRequest(StrictAgentToolModel):
    """Phase 36 derivative 编辑提议 action 请求（36-05，REQ-FORK-02 / REQ-AGENT-03/04/07）。

    只创建**一个候选 DerivativeEditProposal**（D-36-02）：服务端 proposal gate
    只接受冻结 source snapshot 血缘 + 有效 project/chapter scope + base_revision
    CAS 锚；创建候选 proposal（proposal_status=proposed）+ pending Web
    ApprovalRequest（action=apply_derivative_edit，payload_hash 确定性重放，
    D-11/D-15）。novel_id 由查询参数注入（require_owned_novel），绝不放进请求体。
    绝不直接应用——确定性 Revision Service（
    app.services.derivative_editor.revisions.apply_agent_edit）在用户 Web 批准后
    原子校验 approval + payload + 冻结 proposal artifact 血缘 +
    owner/novel/branch/fork scope + 同一 base_revision CAS 才把 approved proposal
    应用为 append-only agent_proposal 修订；Original Canon / user draft
    （autosave）revisions / published 状态绝不被 Agent 触碰。
    """

    branch: str | None = Field(
        default=None, max_length=80, description="衍生分支；原始主线为 null"
    )
    fork: str | None = Field(
        default=None,
        max_length=80,
        description="衍生 fork（仅 derivative mode；original 必须为 null）",
    )
    project_id: int = Field(
        gt=0,
        description="derivative project ID（服务端重验 owner/novel + fanfiction_canon 空间）",
    )
    chapter_id: int = Field(
        gt=0, description="派生 chapter ID（服务端重验 project 范围）"
    )
    chapter_number: int = Field(ge=1, description="派生 chapter 序号（血缘/审计标注）")
    proposal_key: str = Field(
        min_length=1,
        max_length=160,
        description="幂等 proposal 键（approval payload 绑定，重放追溯）",
    )
    base_revision: int = Field(
        gt=0,
        description="chapter 乐观并发 token（同一 base_revision CAS，D-36-02；绝不 last-write-wins）",
    )
    content: str = Field(
        min_length=1,
        max_length=50000,
        description="候选 Markdown patch（Fanfiction Canon draft；服务端计算 content_hash 并绑定 approval payload）",
    )
    source_snapshot_id: str | None = Field(
        default=None,
        max_length=160,
        description="source snapshot 血缘 ID（project 冻结 fork 血缘）",
    )
    source_snapshot_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="source snapshot 血缘 hash（服务端与 project 冻结 fork 血缘重放；drift → fail closed）",
    )
    evidence_refs: list[str] = Field(
        min_length=1,
        description="proposal 引用的 leaf 证据键（必须属于冻结 manifest 白名单）",
    )
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill/artifact 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(
        default=None, gt=0, description="SkillVersion ID 血缘"
    )
    artifact_id: int | None = Field(default=None, gt=0, description="Artifact ID 血缘")
    artifact_revision_id: int | None = Field(
        default=None, gt=0, description="ArtifactRevision ID 血缘"
    )


# ────────────────────────── Phase 37 derivative generation action 工具（37-05） ──────────────────────────


class AllowDivergenceRequest(StrictAgentToolModel):
    """Phase 37 显式 divergence action 请求（37-05，REQ-FORK-03 / REQ-AGENT-03/04/07）。

    只为一个 blocked / ``needs_override`` 生成候选创建**一个显式 divergence
    override**（D-37-03）：服务端 override gate 只接受理由 + 受影响的 leaf 证据
    （或候选已声明的 CanonDelta），并校验调用方携带的 ``draft_hash`` /
    ``canon_delta_hash`` 与候选确定性血缘重放一致（drift → fail closed）。
    创建 pending ``DerivativeOverride`` + pending Web ApprovalRequest
    （action=allow_divergence，payload_hash = canonical hash 绑定 exact
    draft_hash + canon_delta_hash，D-11/D-15）。**绝不发布、绝不写 Original
    Canon**——只有先确认本 approval 再经独立 ``publish_derivative_revision``
    approval 后由确定性 revision publisher 物化。novel_id 由查询参数注入
    （require_owned_novel），绝不放进请求体。
    """

    branch: str | None = Field(
        default=None, max_length=80, description="衍生分支；原始主线为 null"
    )
    fork: str | None = Field(
        default=None,
        max_length=80,
        description="衍生 fork（仅 derivative mode；original 必须为 null）",
    )
    project_id: int = Field(
        gt=0,
        description="derivative project ID（服务端重验 owner/novel + fanfiction_canon 空间）",
    )
    chapter_id: int = Field(
        gt=0, description="派生 chapter ID（服务端重验 project 范围）"
    )
    candidate_id: int = Field(
        gt=0,
        description="generation candidate ID（服务端重验 owner/novel 血缘 + overridable verdict）",
    )
    reason: str = Field(
        min_length=1,
        max_length=4000,
        description="显式 divergence 理由（空 → fail closed）",
    )
    affected_evidence: list[str] = Field(
        default_factory=list,
        description="受影响的 leaf 证据键（必须 ⊆ 冻结 package 白名单；候选已声明 CanonDelta 时可省略）",
    )
    kind: str | None = Field(
        default=None,
        max_length=32,
        description="可选 CanonDelta 类型（候选未声明时必填）",
    )
    draft_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="候选结构化输出的 canonical draft hash（服务端重放；drift → fail closed）",
    )
    canon_delta_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="候选 CanonDelta hash（服务端重放；与 approval payload 绑定）",
    )
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill/artifact 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(
        default=None, gt=0, description="SkillVersion ID 血缘"
    )
    artifact_id: int | None = Field(default=None, gt=0, description="Artifact ID 血缘")
    artifact_revision_id: int | None = Field(
        default=None, gt=0, description="ArtifactRevision ID 血缘"
    )


class PublishDerivativeRevisionRequest(StrictAgentToolModel):
    """Phase 37 独立 publish approval action 请求（37-05，REQ-FORK-03 / REQ-AGENT-03/04/07）。

    只在 **allow_divergence approval 已批准 + 完整 revalidation 通过** 后才为同一
    候选创建一个**独立** pending Web ApprovalRequest（action=
    publish_derivative_revision），绑定**与 allow_divergence approval 完全相同的**
    ``draft_hash`` / ``canon_delta_hash``（相同 hash 绑定；漂移/跳过前序步骤 →
    fail closed）。绝不复用 allow_divergence approval——只有独立 publish approval
    被用户批准后，确定性 revision publisher 才能物化 Fanfiction Canon 修订。
    本工具**绝不发布、绝不写 Original Canon**。novel_id 由查询参数注入
    （require_owned_novel），绝不放进请求体。
    """

    branch: str | None = Field(
        default=None, max_length=80, description="衍生分支；原始主线为 null"
    )
    fork: str | None = Field(
        default=None,
        max_length=80,
        description="衍生 fork（仅 derivative mode；original 必须为 null）",
    )
    override_id: int = Field(
        gt=0,
        description="已存在的 pending DerivativeOverride ID（服务端重验 owner/novel 血缘）",
    )
    draft_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="候选 canonical draft hash（必须与 allow_divergence approval 完全一致）",
    )
    canon_delta_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="候选 CanonDelta hash（必须与 allow_divergence approval 完全一致）",
    )
    approval_note: str | None = Field(
        default=None, max_length=4000, description="供发布 approval 展示的显式批准备注"
    )
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill/artifact 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(
        default=None, gt=0, description="SkillVersion ID 血缘"
    )
    artifact_id: int | None = Field(default=None, gt=0, description="Artifact ID 血缘")
    artifact_revision_id: int | None = Field(
        default=None, gt=0, description="ArtifactRevision ID 血缘"
    )


# ────────────────────────── Phase 38 branch-aware derivative visual action 工具（38-05） ──────────────────────────


class PublishDerivativeVisualRequest(StrictAgentToolModel):
    """Phase 38 derivative visual publish action 请求（38-05，REQ-FORK-04 / REQ-AGENT-03/04/07）。

    只为**一个已存储 derivative candidate asset**创建**一个 pending Web
    ApprovalRequest**（action=publish_derivative_visual，payload_hash 绑定候选
    冻结血缘：asset_id/content_hash/scene_spec_hash/divergence_manifest_hash/
    consistency_verdict/source_snapshot_hash/fork_id，D-11/D-15）。服务端 action
    gate 只接受 owner/novel/fork scope 内可批准（candidate/needs_review）的候选
    （blocked 候选转移集为空 → fail closed）；candidate_id 由调用方携带，scope /
    fork 血缘由服务端从候选行确定性派生。novel_id 由查询参数注入
    （require_owned_novel），绝不放进请求体。**绝不发布**——只有独立 approval
    被用户批准后，确定性 review seam（app.services.derivative_visual.review.
    review_candidate_asset → apply_derivative_asset_review）原子校验 approval +
    payload + fork scope + 合法 review 转移才把 candidate 物化为 approved
    published asset；Original Visual Bible 绝不被触碰。
    """

    branch: str | None = Field(
        default=None, max_length=80, description="衍生分支；原始主线为 null"
    )
    fork: str | None = Field(
        default=None,
        max_length=80,
        description="衍生 fork（仅 derivative mode；original 必须为 null）",
    )
    candidate_asset_id: int = Field(
        gt=0,
        description="已存储 derivative candidate asset ID（服务端重验 owner/novel/fork 血缘 + approvable review_state）",
    )
    scene_spec_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="frozen canonical derivative Scene Spec 血缘 hash（服务端与候选血缘重放；drift → fail closed）",
    )
    approval_note: str | None = Field(
        default=None, max_length=4000, description="供发布 approval 展示的显式批准备注"
    )
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill/artifact 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(
        default=None, gt=0, description="SkillVersion ID 血缘"
    )
    artifact_id: int | None = Field(default=None, gt=0, description="Artifact ID 血缘")
    artifact_revision_id: int | None = Field(
        default=None, gt=0, description="ArtifactRevision ID 血缘"
    )


# ────────────────────────── Phase 39 derivative export action 工具（39-05） ──────────────────────────


class ApproveExportRequest(StrictAgentToolModel):
    """Phase 39 approve_export action 请求（39-05，REQ-FORK-05 / REQ-AGENT-03/04/07）。

    为**一个已 finalize 候选 ExportPreparationArtifact**创建**一个 pending Web
    ApprovalRequest**（action=approve_export，payload_hash 绑定 artifact
    revision + 确定性 preparation_hash，D-11/D-15）。服务端 action 只接受
    owner/novel/branch/fork/project scope 内的 candidate artifact；确定性
    preparation 服务重放冻结 manifest（stale/伪造 hash → fail closed）。
    novel_id 由查询参数注入（require_owned_novel），绝不放进请求体。
    **绝不物化**——只有独立 approve_export approval 被用户批准后，确定性
    materializer（materialize_export）才能把候选 artifact 推进为 approved 并
    产出可复现 bundle；Agent 绝不写 Original Canon / 域表 / Artifact 状态 /
    bundle。
    """

    branch: str | None = Field(
        default=None, max_length=80, description="衍生分支；原始主线为 null"
    )
    fork: str | None = Field(
        default=None,
        max_length=80,
        description="衍生 fork（仅 derivative mode；original 必须为 null）",
    )
    project_id: int = Field(
        gt=0,
        description="derivative project ID（服务端重验 owner/novel + fanfiction_canon 空间）",
    )
    artifact_id: int = Field(
        gt=0,
        description="候选 ExportPreparationArtifact ID（服务端重验 owner/novel + candidate status）",
    )
    artifact_revision_id: int = Field(
        gt=0,
        description="候选 ArtifactRevision ID（approval payload 绑定；必须是当前修订）",
    )
    preparation_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="候选冻结 preparation hash（服务端从 artifact revision + 冻结 manifest 重放；stale → fail closed）",
    )
    approval_note: str | None = Field(
        default=None, max_length=4000, description="供 approval 展示的显式批准备注"
    )
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(
        default=None, gt=0, description="SkillVersion ID 血缘"
    )


class MaterializeExportRequest(StrictAgentToolModel):
    """Phase 39 materialize_export action 请求（39-05，REQ-FORK-05 / REQ-AGENT-03/04/07）。

    确定性 materializer 消费**一个已批准的 approve_export ApprovalRequest**：
    只接受 owner/novel/branch/fork/project scope 内已 finalize 的候选
    ExportPreparationArtifact + preparation_hash 匹配的 approve_export
    approval；服务端原子校验 approval action + 相同 preparation_hash 绑定 +
    artifact revision 血缘 + 冻结 manifest 重放，才把候选 artifact 推进为
    approved 并产出可复现 bundle（frozen manifest 复算）。novel_id 由查询参数
    注入（require_owned_novel），绝不放进请求体。download 只读、永不改变
    Artifact status / approval lineage。forged/expired/cancelled/rejected
    approval、stale hash、wrong scope、pending/rejected artifact → fail
    closed，无 bundle 或权威写入。
    """

    branch: str | None = Field(
        default=None, max_length=80, description="衍生分支；原始主线为 null"
    )
    fork: str | None = Field(
        default=None,
        max_length=80,
        description="衍生 fork（仅 derivative mode；original 必须为 null）",
    )
    project_id: int = Field(
        gt=0,
        description="derivative project ID（服务端重验 owner/novel + fanfiction_canon 空间）",
    )
    artifact_id: int = Field(
        gt=0,
        description="候选 ExportPreparationArtifact ID（只接受 approved artifact）",
    )
    artifact_revision_id: int = Field(
        gt=0,
        description="候选 ArtifactRevision ID（approval payload 绑定；必须是当前修订）",
    )
    approval_id: int = Field(
        gt=0,
        description="已批准的 approve_export ApprovalRequest ID（服务端重验 action + status + preparation_hash）",
    )
    preparation_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="候选冻结 preparation hash（必须与 approve_export approval payload_hash 一致）",
    )
    reason: str | None = Field(
        default=None,
        max_length=4000,
        description="确定性 materialize 理由（展示/审计）",
    )
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(
        default=None, gt=0, description="SkillVersion ID 血缘"
    )


# ────────────────────────── 统一错误信封 ──────────────────────────


class AgentToolErrorBody(BaseModel):
    """稳定错误码体（冻结表见 services/agent_tools/errors.py）。"""

    code: str
    message: str


class AgentToolErrorEnvelope(BaseModel):
    """工具错误统一响应形状：``{"error": {"code", "message"}}``。"""

    error: AgentToolErrorBody
