"""Lineage-bound qualified_candidate/blocked report for the reading-QA gate.

Phase 29-02 / REQ-QA-02; decisions D-02..D-05 from 29-CONTEXT.md:

- D-02: every report binds db fingerprint, dataset version, source snapshot,
  commit, model/prompt/schema and budget.
- D-03: each bucket keeps its own metrics, worst cases and blocked reasons;
  failures are never hidden behind a single aggregate score.
- D-05: the only allowed verdicts are ``qualified_candidate`` and ``blocked``.

The report is a pure JSON-serializable contract with a deterministic checksum;
it never writes to a database and never promotes anything.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.narrative_memory.qualification_contracts import (
    SCOPE_DISCLAIMER,
    stable_json,
)
from app.services.qualification.gold_set import GoldBucket

REPORT_VERSION = "reading-qa-qualification.v1"

VERDICT_QUALIFIED = "qualified_candidate"
VERDICT_BLOCKED = "blocked"
VERDICT_LABELS: tuple[str, ...] = (VERDICT_QUALIFIED, VERDICT_BLOCKED)

# Vocabulary that must never appear in a report (D-05: no promotion).
FORBIDDEN_REPORT_WORDS = frozenset(
    {"promote", "promoted", "promotion", "active_pointer", "production_ready"}
)

BUDGET_KEYS = ("max_calls", "max_input_tokens", "max_output_tokens", "max_cost_usd")


class FrozenReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QualificationHeader(FrozenReportModel):
    """Lineage-bound run header (D-02). Every field is mandatory."""

    db_fingerprint: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    commit: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    config: str = Field(min_length=1)
    budget: dict[str, Any]

    @model_validator(mode="after")
    def _budget_present(self) -> QualificationHeader:
        if not isinstance(self.budget, dict) or not self.budget:
            raise ValueError("header budget must be a non-empty dict")
        if not any(key in self.budget for key in BUDGET_KEYS):
            raise ValueError(
                f"header budget must carry at least one of {BUDGET_KEYS}"
            )
        return self


class WorstCase(FrozenReportModel):
    """One sampled failure or worst-performing case inside a bucket."""

    sample_id: str = Field(min_length=1)
    bucket: GoldBucket
    system: Literal["candidate", "baseline"]
    reason: str = Field(min_length=1)
    detail: str


class BucketReport(FrozenReportModel):
    """Per-bucket metrics for both systems, worst cases and blocked reasons."""

    bucket: GoldBucket
    metrics: dict[str, dict[str, float | int | None]]
    worst_cases: tuple[WorstCase, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    sample_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _systems_present(self) -> BucketReport:
        for system in ("candidate", "baseline"):
            if system not in self.metrics:
                raise ValueError(f"bucket {self.bucket} missing {system} metrics")
        return self


class OperationsReport(FrozenReportModel):
    """Run-level operations metrics (latency / cost / calls / reuse)."""

    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    cost_usd_total: float | None = None
    calls_total: int = 0
    tokens_total: int = 0
    fallback_count: int = 0
    provider_error_count: int = 0
    reused_case_count: int = 0
    reuse_rebuilt_count: int | None = None
    reuse_carried_count: int | None = None
    reuse_stale_count: int | None = None
    observed_actual_cost_usd: float | None = None
    full_rebuild_upper_bound_cost_usd: float | None = None
    avoided_upper_bound_cost_usd: float | None = None


class DimensionSnapshot(FrozenReportModel):
    """Preserved per-dimension status/progress/blocked_reason (Task 3)."""

    dimension: str = Field(min_length=1)
    status: str = Field(min_length=1)
    progress: float = Field(ge=0.0, le=1.0)
    blocked_reason: str | None = None


class ManifestSnapshot(FrozenReportModel):
    """CandidateManifest header + dimension snapshots, checksum-verified."""

    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff: int = Field(gt=0)
    owner_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    version_key: str = Field(min_length=1)
    dimensions: tuple[DimensionSnapshot, ...] = ()


class QualificationReport(FrozenReportModel):
    """The complete lineage-bound qualification report (D-02/D-05)."""

    report_version: str = REPORT_VERSION
    header: QualificationHeader
    buckets: tuple[BucketReport, ...] = ()
    operations: OperationsReport = OperationsReport()
    manifest: ManifestSnapshot | None = None
    blocked_reasons: tuple[str, ...] = ()
    verdict: Literal["qualified_candidate", "blocked"]
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    disclaimer: str = SCOPE_DISCLAIMER

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for key in ("buckets", "blocked_reasons", "manifest"):
                if isinstance(value.get(key), list):
                    value = {**value, key: tuple(value[key])}
        return value

    @model_validator(mode="after")
    def _report_rules(self) -> QualificationReport:
        if self.verdict not in VERDICT_LABELS:
            raise ValueError(f"illegal verdict {self.verdict!r}")
        banned = FORBIDDEN_REPORT_WORDS.intersection(self.blocked_reasons)
        if banned:
            raise ValueError(f"illegal reason vocabulary: {sorted(banned)}")
        ordered = tuple(sorted(self.blocked_reasons))
        if self.blocked_reasons != ordered:
            raise ValueError("blocked_reasons must be sorted")
        if self.verdict == VERDICT_QUALIFIED and self.blocked_reasons:
            raise ValueError("qualified_candidate must carry no blocked_reasons")
        if self.verdict == VERDICT_BLOCKED and not self.blocked_reasons:
            raise ValueError("blocked requires non-empty blocked_reasons")
        return self

    def checksum_payload(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("checksum", None)
        return stable_json(payload)

    @property
    def checksum_valid(self) -> bool:
        return self.checksum == sha256(
            self.checksum_payload().encode("utf-8")
        ).hexdigest()


def report_checksum(report: QualificationReport) -> str:
    """Deterministic SHA-256 over the report without its own checksum field."""
    return sha256(report.checksum_payload().encode("utf-8")).hexdigest()


def build_report(
    *,
    header: QualificationHeader,
    buckets: list[BucketReport],
    operations: OperationsReport,
    manifest: ManifestSnapshot | None,
    blocked_reasons: list[str],
    verdict: str,
) -> QualificationReport:
    """Assemble a report with a stable checksum (fail closed on bad input)."""
    report = QualificationReport(
        header=header,
        buckets=tuple(buckets),
        operations=operations,
        manifest=manifest,
        blocked_reasons=tuple(sorted(set(blocked_reasons))),
        verdict=verdict,  # type: ignore[arg-type]
        checksum="0" * 64,
    )
    return report.model_copy(
        update={"checksum": report_checksum(report)}
    )


def report_has_promotion_capability() -> bool:
    return False


def report_has_provider_capability() -> bool:
    return False
