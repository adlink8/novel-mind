"""Strict frozen contracts for Phase 17 single-book candidate qualification.

Fixture/policy must freeze before any candidate result is observed. Public
verdict is only qualified_candidate | blocked. No promotion/cutover vocabulary.
"""

from __future__ import annotations

import json
import math
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.services.narrative_memory.contracts import Hash64, Key, PositiveInt, VersionLabel

QUALIFICATION_KIND = "single_book_candidate"
SCOPE_DISCLAIMER = (
    "Single-book candidate qualification only. Does not promote, activate, or "
    "cut over timeline/relationship/clue/Reader Chat consumers. Does not claim "
    "closure of v0.3 project-wide 100-confirmed, faithfulness, or cost gaps."
)

# Fields that must never appear in fixture authoring inputs (result-derived).
FORBIDDEN_FIXTURE_RESULT_FIELDS = frozenset(
    {
        "candidate_answer",
        "candidate_trace",
        "candidate_score",
        "candidate_report",
        "baseline_answer",
        "baseline_score",
        "metric_value",
        "metrics",
        "verdict",
        "judge_score",
        "faithfulness_score",
        "relevance_score",
        "retrieval_trace",
        "answer_text",
        "report_checksum",
    }
)


class QualificationFrozenModel(BaseModel):
    """Frozen + forbid-extra; not strict so JSON list/enum strings validate."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_checksum(value: object) -> str:
    """SHA-256 of canonical JSON. Never hash a digest field into itself."""
    if isinstance(value, dict):
        cleaned = {
            k: v
            for k, v in value.items()
            if k
            not in {
                "fixture_checksum",
                "policy_checksum",
                "output_digest",
                "report_checksum",
                "envelope_checksum",
                "metric_report_checksum",
            }
        }
        return sha256(stable_json(cleaned).encode("utf-8")).hexdigest()
    return sha256(stable_json(value).encode("utf-8")).hexdigest()


class QuestionBucket(StrEnum):
    LOCAL = "local"
    CROSS_CHAPTER_ARC = "cross_chapter_arc"
    WHOLE_BOOK_GLOBAL = "whole_book_global"
    NO_ANSWER = "no_answer"
    SPOILER = "spoiler"


REQUIRED_BUCKETS: tuple[QuestionBucket, ...] = (
    QuestionBucket.LOCAL,
    QuestionBucket.CROSS_CHAPTER_ARC,
    QuestionBucket.WHOLE_BOOK_GLOBAL,
    QuestionBucket.NO_ANSWER,
    QuestionBucket.SPOILER,
)


class ExpectedAnswerability(StrEnum):
    ANSWERABLE = "answerable"
    NO_ANSWER = "no_answer"
    SPOILER_RISK = "spoiler_risk"


class RetrievalStrategy(StrEnum):
    HIERARCHICAL_CANDIDATE = "hierarchical_candidate"
    LEAF_RAW_BASELINE = "leaf_raw_baseline"


class QualificationVerdict(StrEnum):
    QUALIFIED_CANDIDATE = "qualified_candidate"
    BLOCKED = "blocked"


class MetricStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    INVALID = "invalid"
    BLOCKED = "blocked"


class GoldLeafRef(QualificationFrozenModel):
    """Exact Phase 07 leaf identity — never a candidate summary or score."""

    leaf_id: Key
    hierarchy_build_id: Key
    source_snapshot_hash: Hash64
    chapter_id: PositiveInt
    chapter_number: PositiveInt
    start_offset: StrictInt = Field(ge=0)
    end_offset: StrictInt = Field(gt=0)
    content_hash: Hash64
    relevance: StrictFloat = Field(ge=0.0, le=3.0)

    @model_validator(mode="after")
    def _offsets(self) -> GoldLeafRef:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be > start_offset")
        if math.isnan(self.relevance) or math.isinf(self.relevance):
            raise ValueError("relevance must be finite")
        return self


class SpoilerForbiddenRef(QualificationFrozenModel):
    """Identity-only forbidden set; no titles or text payloads."""

    leaf_id: Key | None = None
    chapter_number: PositiveInt | None = None
    metadata_key: Key | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> SpoilerForbiddenRef:
        if self.leaf_id is None and self.chapter_number is None and self.metadata_key is None:
            raise ValueError("spoiler forbidden ref requires at least one identity")
        return self


class QuestionCase(QualificationFrozenModel):
    case_key: Key
    bucket: QuestionBucket
    query: Annotated[
        StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    through_chapter: PositiveInt
    full_book_authorized: StrictBool = False
    expected_answerability: ExpectedAnswerability
    allowed_routes: tuple[Key, ...] = ()
    gold_leaves: tuple[GoldLeafRef, ...] = ()
    spoiler_forbidden: tuple[SpoilerForbiddenRef, ...] = ()
    no_answer_rationale: Annotated[
        StrictStr, StringConstraints(max_length=400)
    ] | None = None

    @field_validator(
        "allowed_routes", "gold_leaves", "spoiler_forbidden", mode="before"
    )
    @classmethod
    def _tupleize(cls, v: object) -> object:
        if isinstance(v, list):
            return tuple(v)
        return v

    @model_validator(mode="after")
    def _bucket_rules(self) -> QuestionCase:
        if self.bucket == QuestionBucket.NO_ANSWER:
            if self.expected_answerability != ExpectedAnswerability.NO_ANSWER:
                raise ValueError("no_answer bucket requires expected_answerability=no_answer")
        if self.bucket == QuestionBucket.SPOILER:
            if not self.spoiler_forbidden:
                raise ValueError("spoiler bucket requires non-empty spoiler_forbidden")
            if self.expected_answerability != ExpectedAnswerability.SPOILER_RISK:
                raise ValueError("spoiler bucket requires expected_answerability=spoiler_risk")
        if self.bucket in {
            QuestionBucket.LOCAL,
            QuestionBucket.CROSS_CHAPTER_ARC,
            QuestionBucket.WHOLE_BOOK_GLOBAL,
        }:
            if self.expected_answerability != ExpectedAnswerability.ANSWERABLE:
                raise ValueError(f"{self.bucket} requires answerable cases")
            if not self.gold_leaves:
                raise ValueError(f"{self.bucket} requires non-empty gold_leaves")
        return self


class ModelLineageSpec(QualificationFrozenModel):
    role: Literal["generator", "judge"]
    deployment_id: Key
    model_revision: VersionLabel
    prompt_hash: Hash64
    schema_hash: Hash64
    decoding_hash: Hash64
    calibrated: StrictBool = False

    def lineage_key(self) -> str:
        return f"{self.role}:{self.deployment_id}:{self.model_revision}:{self.prompt_hash}"


class PriceSnapshot(QualificationFrozenModel):
    input_per_million_usd: StrictStr
    output_per_million_usd: StrictStr
    currency: Literal["USD"] = "USD"

    @field_validator("input_per_million_usd", "output_per_million_usd")
    @classmethod
    def _price(cls, v: str) -> str:
        try:
            val = float(v)
        except ValueError as exc:
            raise ValueError("price must be numeric string") from exc
        if math.isnan(val) or math.isinf(val) or val < 0:
            raise ValueError("price must be finite non-negative")
        return v


class BudgetPolicy(QualificationFrozenModel):
    max_calls: PositiveInt = 32
    max_input_tokens: PositiveInt = 50_000
    max_output_tokens: PositiveInt = 20_000
    max_cost_usd: StrictStr = "1.00"
    timeout_ms: PositiveInt = 60_000
    max_retries: StrictInt = Field(ge=0, le=2, default=0)
    top_k: PositiveInt = 8
    max_leaves: PositiveInt = 16
    max_rerank: PositiveInt = 16

    @field_validator("max_cost_usd")
    @classmethod
    def _cost(cls, v: str) -> str:
        try:
            val = float(v)
        except ValueError as exc:
            raise ValueError("max_cost_usd must be numeric") from exc
        if math.isnan(val) or math.isinf(val) or val <= 0:
            raise ValueError("max_cost_usd must be positive finite")
        return v


class ThresholdSpec(QualificationFrozenModel):
    metric_name: Key
    scope: Literal["aggregate", "bucket", "strategy"]
    bucket: QuestionBucket | None = None
    strategy: RetrievalStrategy | None = None
    minimum: StrictFloat | None = None
    maximum: StrictFloat | None = None
    zero_tolerance: StrictBool = False
    relative_to_baseline_min_delta: StrictFloat | None = None

    @model_validator(mode="after")
    def _direction(self) -> ThresholdSpec:
        if self.zero_tolerance:
            return self
        if self.minimum is None and self.maximum is None and self.relative_to_baseline_min_delta is None:
            raise ValueError("threshold requires min, max, zero_tolerance, or relative delta")
        for val in (self.minimum, self.maximum, self.relative_to_baseline_min_delta):
            if val is not None and (math.isnan(val) or math.isinf(val)):
                raise ValueError("threshold bounds must be finite")
        if self.scope == "bucket" and self.bucket is None:
            raise ValueError("bucket scope requires bucket")
        return self


class QualificationPolicy(QualificationFrozenModel):
    policy_version: VersionLabel = "nm-qual-policy.v1"
    qualification_kind: Literal["single_book_candidate"] = QUALIFICATION_KIND
    required_buckets: tuple[QuestionBucket, ...] = REQUIRED_BUCKETS
    min_cases_per_bucket: PositiveInt = 1
    generator: ModelLineageSpec
    judge: ModelLineageSpec
    price: PriceSnapshot
    budget: BudgetPolicy
    metric_version: VersionLabel = "nm-qual-metrics.v1"
    thresholds: tuple[ThresholdSpec, ...]
    disclaimer: StrictStr = SCOPE_DISCLAIMER
    strategy_order: tuple[RetrievalStrategy, ...] = (
        RetrievalStrategy.HIERARCHICAL_CANDIDATE,
        RetrievalStrategy.LEAF_RAW_BASELINE,
    )

    @field_validator("required_buckets", "thresholds", "strategy_order", mode="before")
    @classmethod
    def _tupleize(cls, v: object) -> object:
        if isinstance(v, list):
            return tuple(v)
        return v

    @model_validator(mode="after")
    def _policy_integrity(self) -> QualificationPolicy:
        missing = set(REQUIRED_BUCKETS) - set(self.required_buckets)
        if missing:
            raise ValueError(f"required buckets missing: {sorted(b.value for b in missing)}")
        if self.generator.role != "generator":
            raise ValueError("generator lineage role must be generator")
        if self.judge.role != "judge":
            raise ValueError("judge lineage role must be judge")
        if not self.judge.calibrated:
            raise ValueError("judge must be calibrated")
        if (
            self.generator.deployment_id == self.judge.deployment_id
            and self.generator.model_revision == self.judge.model_revision
            and self.generator.prompt_hash == self.judge.prompt_hash
        ):
            raise ValueError("generator and judge must be isolated lineages")
        if not self.thresholds:
            raise ValueError("policy requires non-empty thresholds")
        if set(self.strategy_order) != set(RetrievalStrategy):
            raise ValueError("strategy_order must include both strategies exactly")
        if "promotion" in self.disclaimer.lower() and "does not promote" not in self.disclaimer.lower():
            pass  # disclaimer text is fixed above
        return self

    def checksum(self) -> str:
        return stable_checksum(self.model_dump(mode="json"))


class QualificationFixture(QualificationFrozenModel):
    fixture_version: VersionLabel = "nm-qual-fixture.v1"
    qualification_kind: Literal["single_book_candidate"] = QUALIFICATION_KIND
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    source_snapshot_hash: Hash64
    hierarchy_build_id: Key
    hierarchy_checksum: Hash64
    candidate_manifest_checksum: Hash64
    reviewed_by: Key
    frozen_at: StrictStr
    cases: tuple[QuestionCase, ...]
    disclaimer: StrictStr = SCOPE_DISCLAIMER

    @field_validator("cases", mode="before")
    @classmethod
    def _tupleize_cases(cls, v: object) -> object:
        if isinstance(v, list):
            return tuple(v)
        return v

    @model_validator(mode="after")
    def _fixture_integrity(self) -> QualificationFixture:
        if not self.cases:
            raise ValueError("fixture requires cases")
        keys = [c.case_key for c in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate case_key")
        counts = {b: 0 for b in REQUIRED_BUCKETS}
        for case in self.cases:
            counts[case.bucket] += 1
            for leaf in case.gold_leaves:
                if leaf.source_snapshot_hash != self.source_snapshot_hash:
                    raise ValueError(
                        f"gold leaf {leaf.leaf_id} cross-snapshot vs fixture"
                    )
                if leaf.hierarchy_build_id != self.hierarchy_build_id:
                    raise ValueError(
                        f"gold leaf {leaf.leaf_id} hierarchy_build_id mismatch"
                    )
                if leaf.chapter_number > case.through_chapter and not case.full_book_authorized:
                    if case.bucket != QuestionBucket.SPOILER:
                        raise ValueError(
                            f"gold leaf {leaf.leaf_id} exceeds cutoff for case {case.case_key}"
                        )
        empty = [b.value for b, n in counts.items() if n == 0]
        if empty:
            raise ValueError(f"empty required buckets: {empty}")
        return self

    def bucket_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {b.value: 0 for b in REQUIRED_BUCKETS}
        for case in self.cases:
            counts[case.bucket.value] += 1
        return counts

    def checksum(self) -> str:
        return stable_checksum(self.model_dump(mode="json"))


class CommonCaseEnvelope(QualificationFrozenModel):
    """Shared fields for both strategies — comparability authority."""

    case_key: Key
    query: StrictStr
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    source_snapshot_hash: Hash64
    hierarchy_build_id: Key
    hierarchy_checksum: Hash64
    candidate_manifest_checksum: Hash64
    through_chapter: PositiveInt
    full_book_authorized: StrictBool
    top_k: PositiveInt
    max_leaves: PositiveInt
    max_rerank: PositiveInt
    generator_lineage_key: Key
    judge_lineage_key: Key
    prompt_hash: Hash64
    schema_hash: Hash64
    decoding_hash: Hash64
    timeout_ms: PositiveInt
    max_retries: StrictInt
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_cost_usd: StrictStr
    price_input_per_million_usd: StrictStr
    price_output_per_million_usd: StrictStr
    metric_version: VersionLabel
    fixture_checksum: Hash64
    policy_checksum: Hash64

    def checksum(self) -> str:
        return stable_checksum(self.model_dump(mode="json"))


class PairedCaseEnvelope(QualificationFrozenModel):
    common: CommonCaseEnvelope
    strategy: RetrievalStrategy
    cache_namespace: Key

    @model_validator(mode="after")
    def _cache_bound(self) -> PairedCaseEnvelope:
        expected = f"{self.strategy.value}:{self.common.case_key}:{self.common.fixture_checksum[:12]}"
        if self.cache_namespace != expected:
            raise ValueError(
                f"cache_namespace must be strategy-scoped identity, expected {expected}"
            )
        return self

    def checksum(self) -> str:
        return stable_checksum(self.model_dump(mode="json"))


class MetricCell(QualificationFrozenModel):
    metric_name: Key
    numerator: StrictFloat | StrictInt
    denominator: StrictFloat | StrictInt
    value: StrictFloat | StrictInt | None
    unit: Key
    status: MetricStatus
    case_ids: tuple[Key, ...] = ()
    bucket: QuestionBucket | None = None
    strategy: RetrievalStrategy | None = None
    note: StrictStr | None = None

    @field_validator("case_ids", mode="before")
    @classmethod
    def _tupleize(cls, v: object) -> object:
        if isinstance(v, list):
            return tuple(v)
        return v

    @model_validator(mode="after")
    def _finite(self) -> MetricCell:
        for label, val in (
            ("numerator", self.numerator),
            ("denominator", self.denominator),
            ("value", self.value),
        ):
            if val is None:
                continue
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                raise ValueError(f"{label} must be finite")
        if self.status == MetricStatus.OK:
            if self.value is None:
                raise ValueError("ok metric requires value")
            if float(self.denominator) <= 0:
                raise ValueError("ok metric requires positive denominator")
        if self.status in {MetricStatus.MISSING, MetricStatus.INVALID, MetricStatus.BLOCKED}:
            if self.value is not None and self.status != MetricStatus.BLOCKED:
                # blocked may carry a computed value for diagnostics
                pass
        return self


class QualificationReport(QualificationFrozenModel):
    qualification_kind: Literal["single_book_candidate"] = QUALIFICATION_KIND
    verdict: QualificationVerdict
    reason_codes: tuple[Key, ...] = ()
    fixture_checksum: Hash64
    policy_checksum: Hash64
    metric_cells: tuple[MetricCell, ...]
    failing_metrics: tuple[Key, ...] = ()
    disclaimer: StrictStr = SCOPE_DISCLAIMER
    pointer_before_digest: Hash64 | None = None
    pointer_after_digest: Hash64 | None = None
    verifier_checksum: Hash64 | None = None

    @field_validator("reason_codes", "metric_cells", "failing_metrics", mode="before")
    @classmethod
    def _tupleize(cls, v: object) -> object:
        if isinstance(v, list):
            return tuple(v)
        return v

    @model_validator(mode="after")
    def _verdict_rules(self) -> QualificationReport:
        if self.verdict not in {
            QualificationVerdict.QUALIFIED_CANDIDATE,
            QualificationVerdict.BLOCKED,
        }:
            raise ValueError("illegal verdict")
        # reject promotion-like vocabulary in reason codes
        banned = {"promoted", "active", "production_ready", "passed", "current"}
        for code in self.reason_codes:
            if code.lower() in banned:
                raise ValueError(f"illegal reason code vocabulary: {code}")
        ordered = tuple(sorted(self.reason_codes))
        if self.reason_codes != ordered:
            raise ValueError("reason_codes must be sorted")
        if self.verdict == QualificationVerdict.QUALIFIED_CANDIDATE and self.reason_codes:
            raise ValueError("qualified_candidate must have empty reason_codes")
        if self.verdict == QualificationVerdict.BLOCKED and not self.reason_codes:
            raise ValueError("blocked requires non-empty reason_codes")
        return self

    def checksum(self) -> str:
        return stable_checksum(self.model_dump(mode="json"))


def reject_result_fields(payload: dict[str, Any]) -> None:
    """Raise if authoring payload contains candidate-result fields."""
    found = sorted(FORBIDDEN_FIXTURE_RESULT_FIELDS.intersection(payload.keys()))
    if found:
        raise ValueError(f"result-derived fields forbidden in fixture: {found}")
    for value in payload.values():
        if isinstance(value, dict):
            reject_result_fields(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    reject_result_fields(item)


def assert_envelopes_paired(
    candidate: PairedCaseEnvelope,
    baseline: PairedCaseEnvelope,
) -> None:
    """Both strategies must share every authoritative field except strategy/cache."""
    if candidate.strategy != RetrievalStrategy.HIERARCHICAL_CANDIDATE:
        raise ValueError("candidate envelope strategy mismatch")
    if baseline.strategy != RetrievalStrategy.LEAF_RAW_BASELINE:
        raise ValueError("baseline envelope strategy mismatch")
    if candidate.common.model_dump() != baseline.common.model_dump():
        raise ValueError("paired envelopes diverge outside strategy/cache")
    if candidate.cache_namespace == baseline.cache_namespace:
        raise ValueError("strategy caches must be isolated")


def build_paired_envelopes(
    case: QuestionCase,
    fixture: QualificationFixture,
    policy: QualificationPolicy,
) -> tuple[PairedCaseEnvelope, PairedCaseEnvelope]:
    common = CommonCaseEnvelope(
        case_key=case.case_key,
        query=case.query,
        owner_id=fixture.owner_id,
        novel_id=fixture.novel_id,
        version_id=fixture.version_id,
        source_snapshot_hash=fixture.source_snapshot_hash,
        hierarchy_build_id=fixture.hierarchy_build_id,
        hierarchy_checksum=fixture.hierarchy_checksum,
        candidate_manifest_checksum=fixture.candidate_manifest_checksum,
        through_chapter=case.through_chapter,
        full_book_authorized=case.full_book_authorized,
        top_k=policy.budget.top_k,
        max_leaves=policy.budget.max_leaves,
        max_rerank=policy.budget.max_rerank,
        generator_lineage_key=policy.generator.lineage_key(),
        judge_lineage_key=policy.judge.lineage_key(),
        prompt_hash=policy.generator.prompt_hash,
        schema_hash=policy.generator.schema_hash,
        decoding_hash=policy.generator.decoding_hash,
        timeout_ms=policy.budget.timeout_ms,
        max_retries=policy.budget.max_retries,
        max_input_tokens=policy.budget.max_input_tokens,
        max_output_tokens=policy.budget.max_output_tokens,
        max_cost_usd=policy.budget.max_cost_usd,
        price_input_per_million_usd=policy.price.input_per_million_usd,
        price_output_per_million_usd=policy.price.output_per_million_usd,
        metric_version=policy.metric_version,
        fixture_checksum=fixture.checksum(),
        policy_checksum=policy.checksum(),
    )
    fx = fixture.checksum()[:12]
    cand = PairedCaseEnvelope(
        common=common,
        strategy=RetrievalStrategy.HIERARCHICAL_CANDIDATE,
        cache_namespace=f"hierarchical_candidate:{case.case_key}:{fx}",
    )
    base = PairedCaseEnvelope(
        common=common,
        strategy=RetrievalStrategy.LEAF_RAW_BASELINE,
        cache_namespace=f"leaf_raw_baseline:{case.case_key}:{fx}",
    )
    assert_envelopes_paired(cand, base)
    return cand, base
