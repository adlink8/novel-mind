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

from pydantic import BaseModel, ConfigDict, Field


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
    """leaf 证据跨度（D-07/D-08）：按 chapter+offsets+content_hash 物化原文。

    只返回冻结原文切片；offsets 非法或 content_hash 与切片不匹配 → 拒绝。
    """

    chapter_id: int = Field(..., gt=0, description="章节 ID")
    source_start: int = Field(..., ge=0, description="切片起点（含，code-point）")
    source_end: int = Field(..., gt=0, description="切片终点（不含）")
    content_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
        description="该切片内容的 SHA-256",
    )


class GetVisualBibleRequest(StrictAgentToolModel):
    """Visual Bible 候选版本视图（31-04，Phase 30 Visual Bible 只读）。

    显式 ``version_id`` → 单个候选信封；缺省 → 版本列表。
    ``approved_only=True`` 只保留 review_state=approved 的版本（D-30-04）；
    approval 权威仍只在 FastAPI review API，本工具只读。
    """

    version_id: int | None = Field(default=None, gt=0, description="Visual Bible 版本 ID")
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
        default="mock", min_length=1, max_length=64, description="提供商（当前仅 mock 配置）"
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
        default=None, max_length=80, description="衍生 fork（仅 derivative mode；original 必须为 null）"
    )
    chapter_id: int = Field(gt=0, description="锚点目标章节 ID（服务端重验 owner/novel 血缘）")
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
    source_start: int = Field(ge=0, description="精确 source span 起点（含，code-point）")
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
        gt=0, description="proposal-ready AssetRevision ID（Phase 33 handoff；服务端重验 proposal_ready + rights cleared）"
    )
    caption: str = Field(min_length=1, max_length=500, description="可访问 caption（D-34-02）")
    alt_text: str = Field(min_length=1, max_length=500, description="可访问 alt 文本（D-34-02）")
    citation: str = Field(min_length=1, max_length=1000, description="引用来源（D-34-02）")
    # D-15 血缘绑定（可选）：agent 会话已知的 run/skill/artifact 血缘。
    run_id: int | None = Field(default=None, gt=0, description="SkillRun ID 血缘")
    skill_version_id: int | None = Field(default=None, gt=0, description="SkillVersion ID 血缘")
    artifact_id: int | None = Field(default=None, gt=0, description="Artifact ID 血缘")
    artifact_revision_id: int | None = Field(
        default=None, gt=0, description="ArtifactRevision ID 血缘"
    )


# ────────────────────────── 统一错误信封 ──────────────────────────


class AgentToolErrorBody(BaseModel):
    """稳定错误码体（冻结表见 services/agent_tools/errors.py）。"""

    code: str
    message: str


class AgentToolErrorEnvelope(BaseModel):
    """工具错误统一响应形状：``{"error": {"code", "message"}}``。"""

    error: AgentToolErrorBody
