"""
Skill Runtime 与 Artifact Contract 持久化权威（25.2-03 / D-09..D-14）。

六张表承载智能体运行时唯一事实源：
  - skill_registry       : 技能目录（owner+novel 范围内技能名唯一）
  - skill_versions       : 不可变技能版本（skill.yaml 契约快照 + yaml_checksum）
  - skill_runs           : 每次技能运行的接受/取消/重试状态机
  - artifacts            : 技能产物（candidate→validated→approved→published→rejected）
  - artifact_revisions   : 不可变产物修订（uq(artifact_id, revision_no)）
  - novel_agent_profiles : 每本小说的智能体配置（D-12）

25.3-04 起第七张表：
  - approval_requests    : Web 审批请求（D-11/D-15，confirm/reject 唯一决策路径）

权威边界（D-01/D-11）:
  - 本模块**不声明任何 ORM 关系属性**：表间通过外键列耦合，杜绝 ORM 层
    意外级联加载与隐式写路径。
  - 会话（Pi session）永远不是事实源；一切长期状态落在这七张表。
  - 审批（ApprovalRequest）状态机在 25.3-04 定义：pending→approved |
    approved_for_session | rejected | expired | cancelled。

遵循 reader_chat.py 模板约定：
  - 可变表（skill_runs / artifacts / novel_agent_profiles / skill_registry）
    使用 ``TimestampMixin, Base``；不可变追加表（skill_versions /
    artifact_revisions）使用裸 ``Base`` + created_at。
  - 每张权威表都携带反规范化 owner_id / novel_id 外键（users.id / novels.id，
    ondelete CASCADE）。
  - 状态元组作为模块常量 + 命名 ``ck_*`` CheckConstraint；校验和 String(64)；
    成本 Numeric(18,8)。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

# 技能版本状态机（draft 表示尚未发布，active 表示当前可用，deprecated 表示已退役）。
SKILL_VERSION_STATUSES = ("draft", "active", "deprecated")
# 技能运行状态机：queued→running→completed/failed/cancelled。
SKILL_RUN_STATUSES = ("queued", "running", "cancelled", "completed", "failed")
# 产物状态机：candidate→validated→approved→published 仅前进，外加 rejected。
ARTIFACT_STATUSES = ("candidate", "validated", "approved", "published", "rejected")
# 审批请求状态机（25.3-04）：pending→approved | approved_for_session | rejected |
# expired | cancelled；已决（非 pending）为终态，拒绝重复决策。
APPROVAL_REQUEST_STATUSES = (
    "pending",
    "approved",
    "approved_for_session",
    "rejected",
    "expired",
    "cancelled",
)


class SkillRegistry(TimestampMixin, Base):
    """技能目录项：owner+novel 范围内技能名唯一。"""

    __tablename__ = "skill_registry"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','deprecated')",
            name="ck_skill_registry_status",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "name",
            name="uq_skill_registry_scope_name",
        ),
        Index("idx_skill_registry_scope", "owner_id", "novel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )


class SkillVersion(Base):
    """不可变技能版本：skill.yaml 契约的持久化快照（D-09）。"""

    __tablename__ = "skill_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','deprecated')",
            name="ck_skill_versions_status",
        ),
        CheckConstraint(
            "length(yaml_checksum) = 64",
            name="ck_skill_versions_yaml_checksum",
        ),
        UniqueConstraint(
            "registry_id",
            "version",
            name="uq_skill_versions_registry_version",
        ),
        Index("idx_skill_versions_scope", "owner_id", "novel_id", "registry_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    registry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skill_registry.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # skill.yaml 内容校验和（String(64)），用于重放追溯。
    yaml_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    # D-09 契约字段（JSONB 持久化）。
    allowed_tools: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    read_permissions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    write_permissions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    forbidden_spaces: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    budget: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    approval_required_for: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    input_schema: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    output_schema: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillRun(TimestampMixin, Base):
    """一次技能运行：接受时冻结 input_hash / budget / manifest，运行后记录终止。"""

    __tablename__ = "skill_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','cancelled','completed','failed')",
            name="ck_skill_runs_status",
        ),
        CheckConstraint("length(input_hash) = 64", name="ck_skill_runs_input_hash"),
        CheckConstraint(
            "retry_count >= 0",
            name="ck_skill_runs_retry_count",
        ),
        Index(
            "idx_skill_runs_scope",
            "owner_id",
            "novel_id",
            "skill_version_id",
        ),
        Index("idx_skill_runs_status", "status"),
        # 问答按需分析（chat_backfill）：同一 owner+novel+维度在途只允许一个。
        Index(
            "uq_skill_runs_backfill_inflight",
            "owner_id",
            "novel_id",
            "backfill_dimension",
            unique=True,
            postgresql_where=text(
                "origin = 'chat_backfill' AND status IN ('queued', 'running')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    skill_version_id: Mapped[int] = mapped_column(
        Integer,
        # RESTRICT：技能版本被产物/运行引用时不可物理删除（血缘保护）。
        ForeignKey("skill_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    status_reason: Mapped[str | None] = mapped_column(String(160))
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    branch: Mapped[str | None] = mapped_column(String(80))
    input: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 冻结于接受时的证据白名单（finalize 用它校验 citations）。
    frozen_manifest: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    model_lineage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    source_versions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    budget_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    # 实际消耗成本（Numeric(18,8)，与 reader_chat 成本列口径一致）。
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    # per-run 内部令牌仅存 SHA-256 哈希（25.2-02 handoff：长时运行越过 JWT 过期）。
    internal_token_hash: Mapped[str | None] = mapped_column(String(64))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    # ── 问答按需分析（chat_backfill）──
    # origin：user_sse=前端 SSE 直连 run；chat_backfill=问答证据不足时按需触发。
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user_sse", server_default="user_sse"
    )
    # 触发维度（QueryDimension 词汇，如 world_projection/raw_text），用于去重与展示。
    backfill_dimension: Mapped[str | None] = mapped_column(String(40))
    # 触发来源用户消息（abstain 的那条），供前端 MessageView 展示 backfill 状态。
    user_message_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("reader_messages.id", ondelete="SET NULL"),
        nullable=True,
    )


class ArtifactRevision(Base):
    """不可变产物修订：uq(artifact_id, revision_no)，内容只增不改。"""

    __tablename__ = "artifact_revisions"
    __table_args__ = (
        CheckConstraint(
            "revision_no >= 1",
            name="ck_artifact_revisions_revision_no",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_artifact_revisions_content_hash",
        ),
        UniqueConstraint(
            "artifact_id",
            "revision_no",
            name="uq_artifact_revisions_revision",
        ),
        Index("idx_artifact_revisions_artifact", "artifact_id", "revision_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 自引用：修订形成链式血缘；首个修订为 NULL。
    parent_revision_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("artifact_revisions.id", ondelete="SET NULL")
    )
    evidence_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Artifact(TimestampMixin, Base):
    """智能体产物：血缘绑定的候选答案（D-10/D-14）。"""

    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate','validated','approved','published','rejected')",
            name="ck_artifacts_status",
        ),
        CheckConstraint("length(input_hash) = 64", name="ck_artifacts_input_hash"),
        Index("idx_artifacts_scope", "owner_id", "novel_id", "run_id"),
        Index("idx_artifacts_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    skill_version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("skill_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skill_runs.id", ondelete="RESTRICT"), nullable=False
    )
    branch: Mapped[str | None] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    model_lineage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    source_versions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 当前修订指针（与 artifact_revisions 互相引用；迁移中用 ALTER TABLE 补外键）。
    # use_alter=True：SQLite drop_all 无法排序循环外键，标记为已知循环。
    current_revision_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("artifact_revisions.id", ondelete="SET NULL", use_alter=True),
    )


class NovelAgentProfile(TimestampMixin, Base):
    """每本小说的智能体配置（D-12）：存版本引用，绝不存内容。"""

    __tablename__ = "novel_agent_profiles"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            name="uq_novel_agent_profiles_scope",
        ),
        Index("idx_novel_agent_profiles_scope", "owner_id", "novel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    agent_profile_version: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled_skills: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    world_model_version: Mapped[str | None] = mapped_column(String(64))
    narrative_memory_version: Mapped[str | None] = mapped_column(String(64))
    visual_bible_version: Mapped[str | None] = mapped_column(String(64))
    reading_cutoff: Mapped[int | None] = mapped_column(Integer)
    active_derivative_branch: Mapped[str | None] = mapped_column(String(80))
    recent_artifacts: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )


class ApprovalRequest(TimestampMixin, Base):
    """Web 审批请求（25.3-04 / D-11 / D-15 / REQ-AGENT-07）。

    - 唯一决策权威在 FastAPI：confirm/reject 是**唯一**状态变更路径
      （services/agent_runtime/approvals.py），SSE 帧只通知、浏览器只渲染。
    - 反规范化 owner_id（users.id ondelete CASCADE）+ D-15 绑定字段；血缘敏感
      外键（run/skill_version/artifact/revision）用 ondelete SET NULL——
      审批记录不阻塞血缘清理。
    - 不声明任何 relationship()（与 house 约定一致，杜绝隐式级联加载/写路径）。
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','approved_for_session','rejected',"
            "'expired','cancelled')",
            name="ck_approval_requests_status",
        ),
        CheckConstraint(
            "payload_hash IS NULL OR length(payload_hash) = 64",
            name="ck_approval_requests_payload_hash",
        ),
        Index("idx_approval_requests_scope", "owner_id", "run_id"),
        Index("idx_approval_requests_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("skill_runs.id", ondelete="SET NULL")
    )
    skill_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("skill_versions.id", ondelete="SET NULL")
    )
    artifact_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    artifact_revision_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("artifact_revisions.id", ondelete="SET NULL")
    )
    novel_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE")
    )
    # D-15 绑定字段（当前无独立 branch/fork 表 → 普通列，不做 FK）。
    branch_id: Mapped[int | None] = mapped_column(Integer)
    fork_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    # 规范化载荷摘要（浏览器渲染用；不承载原始工具 I/O）。
    payload_summary: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    # D-15 重放追溯哈希（可空）。
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    # 决策 actor/time（D-15）：谁、何时做了决定。
    decision_actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
