"""
评测相关 ORM 模型 — RAG 检索质量自动化评测

表:
  - eval_datasets  : 评测数据集（测试题），标记 gold chunks 和期望要点
  - eval_runs      : 评测运行记录（策略、指标汇总）
  - eval_results   : 每条评测的详细结果（召回 chunks、评分）
  - rag_source_snapshots : 冻结源快照（content-hash 真值）
  - rag_fixture_jobs     : fixture 生成作业状态机
  - rag_eval_cases       : 冻结/检疫 EvalCase 持久化

状态枚举:
  - EvalDataset.status : candidate / confirmed / rejected
  - EvalRun.strategy   : baseline_vector / hybrid_search / hybrid_worker
  - RagFixtureJob.status : snapshot_ready…frozen / quarantined / invalid_*
"""

from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.novel import Novel


class EvalDataset(TimestampMixin, Base):
    """评测测试题数据集

    每条记录是一条测试题，记录 gold_chunks（期望召回的 chunk ID 列表）、
    expected_points（应出现在答案中的要点）、must_not_say（不应出现在答案中的内容）。
    """

    __tablename__ = "eval_datasets"
    __table_args__ = (
        Index("idx_eval_datasets_novel_id", "novel_id"),
        Index("idx_eval_datasets_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="original_text"
    )
    difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium"
    )
    gold_chunks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expected_points: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    must_not_say: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="candidate"
    )
    created_by: Mapped[str | None] = mapped_column(String(50), default="auto")

    # 关系
    novel: Mapped["Novel"] = relationship(back_populates="eval_datasets")
    eval_results: Mapped[list["EvalResult"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", lazy="selectin"
    )


class EvalRun(TimestampMixin, Base):
    """评测运行记录

    一次运行 = 一种策略在指定评测集上的执行结果。
    包含汇总指标和性能数据。
    """

    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("idx_eval_runs_novel_id", "novel_id"),
        Index("idx_eval_runs_strategy", "strategy"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_name: Mapped[str] = mapped_column(String(200), nullable=False)
    strategy: Mapped[str] = mapped_column(
        String(50), nullable=False, default="hybrid_search"
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recall_at_k: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    precision_at_k: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    mrr: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    ndcg_at_k: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    faithfulness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # 关系
    novel: Mapped["Novel"] = relationship(back_populates="eval_runs")
    results: Mapped[list["EvalResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )


class EvalResult(TimestampMixin, Base):
    """单条评测结果

    对应一条测试题在一次评测运行中的检索结果。
    记录实际召回的 chunks、答案文本、各项指标。
    """

    __tablename__ = "eval_results"
    __table_args__ = (
        Index("idx_eval_results_run_id", "run_id"),
        Index("idx_eval_results_dataset_id", "dataset_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eval_datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    recalled_chunks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_error_case: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 关系
    run: Mapped["EvalRun"] = relationship(back_populates="results")
    dataset: Mapped["EvalDataset"] = relationship(back_populates="eval_results")


# ── Phase 06-03 RAG quality fixture tables ───────────────────────────


class RagSourceSnapshot(TimestampMixin, Base):
    """Immutable content-hash source snapshot for RAG quality fixtures."""

    __tablename__ = "rag_source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "work_id",
            "snapshot_id",
            name="uq_rag_source_snapshots_scope",
        ),
        UniqueConstraint(
            "manifest_hash",
            name="uq_rag_source_snapshots_manifest",
        ),
        Index("idx_rag_source_snapshots_owner", "owner_id"),
        Index("idx_rag_source_snapshots_work", "work_id"),
        Index("idx_rag_source_snapshots_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    canonicalization_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="rag-canon.v1"
    )
    chunks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="frozen")

    jobs: Mapped[list["RagFixtureJob"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", lazy="selectin"
    )
    cases: Mapped[list["RagEvalCase"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", lazy="selectin"
    )


class RagFixtureJob(TimestampMixin, Base):
    """Durable fixture generation job (snapshot_ready → frozen | quarantined)."""

    __tablename__ = "rag_fixture_jobs"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_rag_fixture_jobs_job_id"),
        Index("idx_rag_fixture_jobs_owner", "owner_id"),
        Index("idx_rag_fixture_jobs_status", "status"),
        Index("idx_rag_fixture_jobs_snapshot", "snapshot_pk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_pk: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("rag_source_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="snapshot_ready"
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    case_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_comparable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    generator_lineage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    judge_lineage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped["RagSourceSnapshot"] = relationship(back_populates="jobs")


class RagEvalCase(TimestampMixin, Base):
    """Persisted EvalCase (frozen or terminal fail states)."""

    __tablename__ = "rag_eval_cases"
    __table_args__ = (
        UniqueConstraint("case_id", "fixture_hash", name="uq_rag_eval_cases_case_hash"),
        Index("idx_rag_eval_cases_case_id", "case_id"),
        Index("idx_rag_eval_cases_status", "status"),
        Index("idx_rag_eval_cases_snapshot", "snapshot_pk"),
        Index("idx_rag_eval_cases_owner", "owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_pk: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("rag_source_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default="rag-quality.v1"
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    case_type: Mapped[str] = mapped_column(String(30), nullable=False)
    claims: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    equivalent_evidence_sets: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    reference_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    generator_lineage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    judge_lineage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    judge_fixture_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deterministic_checks: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fixture_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_case_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    regeneration_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    snapshot: Mapped["RagSourceSnapshot"] = relationship(back_populates="cases")


# ── Phase 06-08 durable quality run + chunker/source lineage ─────────


class QualityRun(TimestampMixin, Base):
    """Persisted durable quality job (lease / checkpoint / stage cache / report).

    Five-tuple lineage (chunker_name, chunker_version, chunker_config_hash,
    chunk_manifest_hash, source_snapshot_hash) is required for quality_comparable.
    Legacy / incomplete rows stay readable with quality_comparable=false.
    """

    __tablename__ = "quality_runs"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_quality_runs_job_id"),
        Index("idx_quality_runs_owner", "owner_id"),
        Index("idx_quality_runs_status", "status"),
        Index("idx_quality_runs_owner_status", "owner_id", "status"),
        Index("idx_quality_runs_lease_expires", "lease_expires_at"),
        # Fail-closed: comparable rows must carry complete five-tuple lineage.
        CheckConstraint(
            "(quality_comparable = false) OR ("
            "chunker_name IS NOT NULL AND length(trim(chunker_name)) > 0 AND "
            "chunker_version IS NOT NULL AND length(trim(chunker_version)) > 0 AND "
            "chunker_config_hash IS NOT NULL AND length(chunker_config_hash) = 64 AND "
            "chunk_manifest_hash IS NOT NULL AND length(chunk_manifest_hash) = 64 AND "
            "source_snapshot_hash IS NOT NULL AND length(source_snapshot_hash) = 64"
            ")",
            name="ck_quality_runs_comparable_requires_lineage",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    stage_cache: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Five-tuple chunker / source lineage (nullable only for legacy/incomparable)
    chunker_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chunker_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunker_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_comparable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    incomparable_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Phase 06-09 baseline candidate prepare/commit + active pointer ───


class BaselineCandidate(TimestampMixin, Base):
    """Two-phase baseline promotion candidate (prepare evidence + commit journal).

    Prepare freezes lineage/hashes/metrics fingerprint from a QualityRun.
    Commit reloads DB state and revalidates before moving the active pointer.
    """

    __tablename__ = "baseline_candidates"
    __table_args__ = (
        UniqueConstraint("prepare_token", name="uq_baseline_candidates_prepare_token"),
        Index("idx_baseline_candidates_owner", "owner_id"),
        Index("idx_baseline_candidates_run", "quality_run_id"),
        Index("idx_baseline_candidates_owner_state", "owner_id", "state"),
        Index(
            "idx_baseline_candidates_snapshot",
            "owner_id",
            "source_snapshot_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    quality_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quality_runs.id", ondelete="RESTRICT"), nullable=False
    )
    quality_run_job_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prepare_token: Mapped[str] = mapped_column(String(64), nullable=False)
    prepare_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="prepared"
    )  # prepared | committed | rejected | expired
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Frozen five-tuple lineage at prepare time
    chunker_name: Mapped[str] = mapped_column(String(128), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Frozen run identity
    run_status: Mapped[str] = mapped_column(String(40), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    metrics_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    prepare_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    journal: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ActiveBaseline(TimestampMixin, Base):
    """Per-owner active baseline pointer (only updated on successful commit)."""

    __tablename__ = "active_baselines"
    __table_args__ = (
        UniqueConstraint("owner_id", name="uq_active_baselines_owner"),
        Index("idx_active_baselines_candidate", "candidate_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("baseline_candidates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quality_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("quality_runs.id", ondelete="RESTRICT"), nullable=False
    )
    metrics_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    chunker_name: Mapped[str] = mapped_column(String(128), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

