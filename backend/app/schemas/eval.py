"""
评测模块 Pydantic Schema — API 请求/响应模型 + RAG quality fixture contracts

验证规则:
  - question: 1-2000 字符
  - question_type: 必须在允许列表中
  - status: candidate / confirmed / rejected
  - strategy: baseline_vector / hybrid_search / hybrid_worker

Phase 06-03 RAG quality contracts (AI-SPEC rag-quality.v1):
  SourceSnapshot, EvidenceRef, EvalCase, ModelLineage, JudgeFixtureVerdict,
  CalibrationCase/Report — truth is content hash + offsets, never DB IDs alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── EvalDataset ──────────────────────────────────────────────────────

class EvalDatasetCreate(BaseModel):
    """创建评测测试题"""
    novel_id: int = Field(..., ge=1, description="小说 ID")
    question: str = Field(..., min_length=1, max_length=2000, description="测试问题")
    question_type: Literal[
        "original_text", "character_relation", "event_causality", "timeline", "foreshadowing"
    ] = Field(
        default="original_text",
        description="题型: original_text / character_relation / event_causality / timeline / foreshadowing",
    )
    difficulty: Literal["easy", "medium", "hard"] = Field(
        default="medium", description="难度: easy / medium / hard"
    )
    gold_chunks: list[int] = Field(
        default_factory=list, description="期望召回的 chunk ID 列表"
    )
    expected_points: list[str] = Field(
        default_factory=list, description="答案中应包含的要点"
    )
    must_not_say: list[str] = Field(
        default_factory=list, description="答案中不应包含的内容"
    )
    status: Literal["candidate", "confirmed", "rejected"] = Field(
        default="candidate", description="审核状态: candidate / confirmed / rejected"
    )
    created_by: str | None = Field(default="auto", description="创建者标识")


class EvalDatasetUpdate(BaseModel):
    """更新评测测试题（人工审核）"""
    question: str | None = Field(None, min_length=1, max_length=2000)
    question_type: Literal[
        "original_text", "character_relation", "event_causality", "timeline", "foreshadowing"
    ] | None = None
    difficulty: Literal["easy", "medium", "hard"] | None = None
    gold_chunks: list[int] | None = None
    expected_points: list[str] | None = None
    must_not_say: list[str] | None = None
    status: Literal["candidate", "confirmed", "rejected"] | None = Field(
        None, description="candidate / confirmed / rejected"
    )


class EvalDatasetResponse(BaseModel):
    """评测测试题响应"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    question: str
    question_type: str
    difficulty: str
    gold_chunks: list
    expected_points: list
    must_not_say: list
    status: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


# ── EvalRun ──────────────────────────────────────────────────────────

class EvalRunCreate(BaseModel):
    """创建评测运行"""
    run_name: str = Field(..., min_length=1, max_length=200)
    strategy: Literal["bm25", "baseline_vector", "hybrid_search"] = Field(
        default="hybrid_search",
        description="检索策略: bm25 / baseline_vector / hybrid_search",
    )
    novel_id: int = Field(..., ge=1)
    dataset_ids: list[int] = Field(
        ..., min_length=1, description="要评测的测试题 ID 列表"
    )


class EvalRunResponse(BaseModel):
    """评测运行响应"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_name: str
    strategy: str
    novel_id: int
    total_questions: int
    recall_at_k: float
    precision_at_k: float | None = None
    mrr: float | None = None
    ndcg_at_k: float | None = None
    faithfulness_score: float | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    config_snapshot: dict
    created_at: datetime
    updated_at: datetime


# ── EvalResult ───────────────────────────────────────────────────────

class EvalResultResponse(BaseModel):
    """单条评测结果响应"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    dataset_id: int
    recalled_chunks: list
    answer_text: str | None = None
    score: float
    metrics: dict
    is_error_case: bool
    created_at: datetime


# ── EvalReport ───────────────────────────────────────────────────────

class EvalReportResponse(BaseModel):
    """评测报告聚合响应"""
    run: EvalRunResponse
    results: list[EvalResultResponse]
    error_cases: list[EvalResultResponse] = []
    summary: dict = {}


# =====================================================================
# Phase 06-03 — RAG quality fixture / calibration contracts
# =====================================================================

SCHEMA_VERSION_RAG_QUALITY = "rag-quality.v1"
CANONICALIZATION_VERSION = "rag-canon.v1"

CaseType = Literal["answerable", "no_answer", "hard_negative"]
FixtureStatus = Literal[
    "snapshot_ready",
    "generating",
    "deterministic_validation",
    "judge_review",
    "frozen",
    "quarantined",
    "invalid_fixture",
    "invalid_lineage",
    "failed_policy",
]
CalibrationCategory = Literal[
    "supported",
    "partial",
    "unsupported",
    "contradictory",
    "no_answer",
    "hard_negative",
    "equivalent_evidence",
]
TerminalFailStatus = Literal[
    "invalid_fixture",
    "invalid_lineage",
    "failed_policy",
    "quarantined",
    "blocked_dependency",
]


class SnapshotChunk(BaseModel):
    """Chunk entry inside a frozen source snapshot (content-hash truth)."""

    content_hash: str = Field(..., min_length=64, max_length=64)
    text_hash: str = Field(..., min_length=64, max_length=64)
    length: int = Field(..., ge=0)
    text: str | None = Field(
        default=None,
        description="Optional inline text for offline validation; not part of manifest hash",
    )


class SourceSnapshot(BaseModel):
    """Frozen source snapshot — truth is content hash, not DB autoincrement IDs."""

    snapshot_id: str = Field(..., min_length=1, max_length=128)
    owner_id: int = Field(..., ge=1)
    work_id: int = Field(..., ge=1, description="Novel/work id under owner scope")
    version: str = Field(..., min_length=1, max_length=64)
    canonicalization_version: str = Field(default=CANONICALIZATION_VERSION)
    chunks: list[SnapshotChunk] = Field(..., min_length=1)
    manifest_hash: str = Field(..., min_length=64, max_length=64)
    created_at: datetime
    signature: str = Field(..., min_length=1)


class EvidenceRef(BaseModel):
    """Evidence bound by chunk content hash + character offsets + quote hash."""

    chunk_content_hash: str = Field(..., min_length=64, max_length=64)
    start_offset: int = Field(..., ge=0)
    end_offset: int = Field(..., ge=0)
    quote_hash: str = Field(..., min_length=64, max_length=64)
    quote_text: str | None = None

    @field_validator("end_offset")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:
        start = info.data.get("start_offset")
        if start is not None and v < start:
            raise ValueError("end_offset must be >= start_offset")
        return v


class Claim(BaseModel):
    claim_id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1)
    critical: bool = False
    evidence_set_ids: list[str] = Field(default_factory=list)


class EquivalentEvidenceSet(BaseModel):
    set_id: str = Field(..., min_length=1, max_length=64)
    refs: list[EvidenceRef] = Field(..., min_length=1)


class DecodingParams(BaseModel):
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    seed: int | None = None


class ModelLineage(BaseModel):
    """Resolved model identity. Alias-only identity is invalid_lineage."""

    model_config = ConfigDict(populate_by_name=True)

    provider: str = Field(..., min_length=1)
    model_family: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1)
    weights_revision: str = Field(
        ...,
        min_length=1,
        alias="weights/revision",
        description="Actual weights/revision; must differ between Generator and Judge",
    )
    endpoint_class: str = Field(default="offline_stub")
    prompt_hash: str = Field(..., min_length=64, max_length=64)
    prompt_version: str = Field(..., min_length=1)
    schema_hash: str = Field(..., min_length=64, max_length=64)
    decoding: DecodingParams = Field(default_factory=DecodingParams)
    runtime: str = Field(default="offline")
    started_at: datetime


class JudgeFixtureVerdict(BaseModel):
    """Independent Judge rubric for fixture review (0..4 each)."""

    faithfulness: int = Field(..., ge=0, le=4)
    coverage: int = Field(..., ge=0, le=4)
    sufficiency: int = Field(..., ge=0, le=4)
    critical_ambiguity: int = Field(..., ge=0)
    reason_codes: list[str] = Field(default_factory=list)
    accepted: bool | None = None


class DeterministicCheckResult(BaseModel):
    name: str
    passed: bool
    detail: str | None = None


class DeterministicChecks(BaseModel):
    schema_ok: bool = False
    snapshot_hash_ok: bool = False
    offset_quote_ok: bool = False
    claims_ok: bool = False
    critical_claim_support_ok: bool = False
    equivalent_sets_ok: bool = False
    leak_ok: bool = False
    no_answer_ok: bool = False
    hard_negative_ok: bool = False
    details: list[DeterministicCheckResult] = Field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(
            [
                self.schema_ok,
                self.snapshot_hash_ok,
                self.offset_quote_ok,
                self.claims_ok,
                self.critical_claim_support_ok,
                self.equivalent_sets_ok,
                self.leak_ok,
                self.no_answer_ok,
                self.hard_negative_ok,
            ]
        )


class EvalCase(BaseModel):
    """Frozen / in-progress RAG quality evaluation case (content-hash truth)."""

    case_id: str = Field(..., min_length=1, max_length=128)
    schema_version: str = Field(default=SCHEMA_VERSION_RAG_QUALITY)
    snapshot_hash: str = Field(..., min_length=64, max_length=64)
    question: str = Field(..., min_length=1, max_length=4000)
    case_type: CaseType
    claims: list[Claim] = Field(default_factory=list)
    equivalent_evidence_sets: list[EquivalentEvidenceSet] = Field(default_factory=list)
    reference_answer: str | None = None
    generator_lineage: ModelLineage | None = None
    judge_lineage: ModelLineage | None = None
    judge_fixture_verdict: JudgeFixtureVerdict | None = None
    deterministic_checks: DeterministicChecks | None = None
    fixture_hash: str | None = None
    signature: str | None = None
    status: FixtureStatus = "snapshot_ready"
    parent_case_id: str | None = None
    attempt: int = Field(default=0, ge=0, le=2)
    regeneration_reason: str | None = None
    # Explicitly reject gold DB ids as sole truth (legacy migration only).
    gold_chunk_db_ids: list[int] | None = Field(
        default=None,
        description="Legacy-only; qualification rejects cases with only these",
    )


class FixtureJobState(BaseModel):
    """Pipeline job for snapshot_ready -> frozen | quarantined."""

    job_id: str
    owner_id: int
    work_id: int
    snapshot_id: str
    status: FixtureStatus
    attempt: int = 0
    case_id: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    metrics: dict[str, Any] | None = None
    quality_comparable: bool = False
    error_detail: str | None = None


class FailClosedResult(BaseModel):
    """Canonical fail-closed outcome for adversarial / lineage / policy failures."""

    status: TerminalFailStatus
    metrics: None = None
    quality_comparable: bool = False
    reason: str
    detail: dict[str, Any] = Field(default_factory=dict)


class CalibrationCase(BaseModel):
    """Independent Judge calibration item (not benchmark domain/hash)."""

    case_id: str
    category: CalibrationCategory
    question: str
    candidate_answer: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    gold_verdict: Literal[
        "accept", "reject", "partial", "contradictory", "no_answer", "hard_negative"
    ]
    critical: bool = False


class CalibrationSuite(BaseModel):
    schema_version: str = SCHEMA_VERSION_RAG_QUALITY
    suite_id: str
    domain: str = Field(..., min_length=1)
    suite_type: Literal["calibration"] = "calibration"
    cases: list[CalibrationCase] = Field(..., min_length=1)
    suite_hash: str
    signature: str
    prompt_hash: str
    schema_hash: str


class CalibrationReport(BaseModel):
    suite_hash: str
    suite_signature: str
    prompt_hash: str
    schema_hash: str
    judge_lineage: ModelLineage
    domain: str
    repeats: int = 3
    confusion_matrix: dict[str, dict[str, int]]
    critical_false_accept: int
    consistency: float
    status: Literal["passed", "invalid_lineage"]
    metrics: dict[str, Any] | None = None
    quality_comparable: bool = False
    signature: str | None = None


# ── Phase 06-08 chunker / source five-tuple lineage ───────────────────

LEGACY_INCOMPARABLE_REASON = "legacy_incomparable"
INVALID_LINEAGE_REASON = "invalid_lineage"


class ChunkerLineage(BaseModel):
    """Canonical five-tuple chunker/source lineage for quality comparability.

    Caller-supplied ``chunker_config_hash`` is untrusted; services recompute it
    from ``chunker_config`` via stable canonical JSON before hashing identities.
    """

    chunker_name: str = Field(..., min_length=1, max_length=128)
    chunker_version: str = Field(..., min_length=1, max_length=64)
    chunker_config: dict[str, Any] = Field(default_factory=dict)
    # Untrusted if provided by caller — always recomputed for comparable runs.
    chunker_config_hash: str | None = Field(default=None, min_length=64, max_length=64)
    chunk_manifest_hash: str = Field(..., min_length=64, max_length=64)
    source_snapshot_hash: str = Field(..., min_length=64, max_length=64)

    def five_tuple(self) -> dict[str, str]:
        """Return only the five canonical identity fields (config hash required)."""
        if not self.chunker_config_hash:
            raise ValueError("chunker_config_hash must be set before five_tuple()")
        return {
            "chunker_name": self.chunker_name,
            "chunker_version": self.chunker_version,
            "chunker_config_hash": self.chunker_config_hash,
            "chunk_manifest_hash": self.chunk_manifest_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
        }


class QualityRunPublic(BaseModel):
    """API-facing durable quality run status (06-08)."""

    job_id: str
    owner_id: int
    status: str
    attempt: int = 0
    input_hash: str | None = None
    output_hash: str | None = None
    report_signature: str | None = None
    quality_comparable: bool = False
    metrics: dict[str, Any] | None = None
    error_detail: str | None = None
    incomparable_reason: str | None = None
    checkpoint_stage: str | None = None
    lease_held: bool = False
    cancel_requested: bool = False
    chunker_name: str | None = None
    chunker_version: str | None = None
    chunker_config_hash: str | None = None
    chunk_manifest_hash: str | None = None
    source_snapshot_hash: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    report: dict[str, Any] | None = None


# ── Phase 06-09 baseline prepare/commit + cross-chunker report ───────

BASELINE_ELIGIBLE_STATUSES = frozenset({"passed", "qualified"})


class BaselinePrepareRequest(BaseModel):
    """Prepare a baseline candidate from a durable QualityRun job_id."""

    job_id: str = Field(..., min_length=1, max_length=128)


class BaselineCommitRequest(BaseModel):
    """Commit a prepared candidate; server reloads DB state (payload not trusted)."""

    candidate_id: int = Field(..., ge=1)
    prepare_token: str = Field(..., min_length=8, max_length=64)


class BaselineCandidatePublic(BaseModel):
    id: int
    owner_id: int
    quality_run_id: int
    quality_run_job_id: str
    prepare_token: str
    prepare_version: int = 1
    state: str
    reason: str | None = None
    chunker_name: str
    chunker_version: str
    chunker_config_hash: str
    chunk_manifest_hash: str
    source_snapshot_hash: str
    run_status: str
    input_hash: str
    output_hash: str
    report_signature: str
    metrics_snapshot: dict[str, Any]
    prepare_fingerprint: str
    journal: list[dict[str, Any]] = Field(default_factory=list)
    prepared_at: str | None = None
    committed_at: str | None = None


class ActiveBaselinePublic(BaseModel):
    owner_id: int
    candidate_id: int
    quality_run_id: int
    metrics_snapshot: dict[str, Any]
    chunker_name: str
    chunker_version: str
    chunker_config_hash: str
    chunk_manifest_hash: str
    source_snapshot_hash: str
    committed_at: str | None = None


class CrossChunkerReportRequest(BaseModel):
    """Aggregate comparable QualityRuns for one source snapshot."""

    source_snapshot_hash: str = Field(..., min_length=64, max_length=64)


class CrossChunkerSeriesItem(BaseModel):
    job_id: str
    quality_run_id: int
    status: str
    chunker_name: str
    chunker_version: str
    chunker_config_hash: str
    chunk_manifest_hash: str
    source_snapshot_hash: str
    metrics: dict[str, Any]
    input_hash: str | None = None
    output_hash: str | None = None
    report_signature: str | None = None
    cost_usd_total: float | None = None
    latency_ms_total: float | None = None


class CrossChunkerExclusion(BaseModel):
    job_id: str | None = None
    quality_run_id: int | None = None
    reason: str


class CrossChunkerReportResponse(BaseModel):
    source_snapshot_hash: str
    series: list[CrossChunkerSeriesItem]
    exclusions: list[CrossChunkerExclusion] = Field(default_factory=list)

