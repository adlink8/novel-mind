"""Reading QA gold-set loader and reproducible dataset fingerprint (Phase 29-01).

REQ-QA-01 / decisions D-01, D-02, D-04, D-05 from 29-CONTEXT.md.

Frozen single-book gold set covering eight question buckets: local,
cross-chapter, global, causal, character-knowledge, world-rule, no-answer and
spoiler. Every sample binds source answers to leaf evidence refs (chapter +
Unicode code-point offsets + content hash, re-sliced from the frozen source
snapshot) and to a spoiler cutoff; the dataset fingerprint and curator agreement
are deterministic and reproducible.

Fail-closed guarantees:

- The dataset fingerprint is recomputed from canonical JSON (the ``fingerprint``
  field itself is excluded); any stored-vs-computed mismatch rejects the set.
- Every gold evidence ref is re-sliced against the frozen chapter text; stale
  hashes, foreign chapters, cross-snapshot refs and invalid offsets reject.
- Gold only uses original text: candidate answers / scores / reports are
  forbidden in the frozen payload.
- Curator agreement is deterministic over per-sample curator ratings; frozen
  sets require unanimous agreement.
- Lineage (owner / novel / version / source snapshot) is mandatory and every
  gold ref is bound to the dataset snapshot (owner/novel/version boundary).

Pure module: no database, no network, no provider calls.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GOLD_SCHEMA_VERSION = "reading-qa-gold.v1"
DEFAULT_DATASET_VERSION = "reading-qa.v1"
PARTITION_FROZEN = "frozen"

# Canonical four-label epistemic authority (Phase 27-04 / queryplan contracts).
AUTHORITY_LABELS: tuple[str, ...] = (
    "canon_fact",
    "probable_inference",
    "literary_interpretation",
    "user_interpretation",
)

ANSWERABILITY_LABELS: tuple[str, ...] = ("answerable", "no_answer", "spoiler_risk")
CUTOFF_LABELS: tuple[str, ...] = ("within_cutoff", "at_cutoff", "forbidden")

# Result-derived fields that must never appear in a frozen gold payload.
FORBIDDEN_GOLD_RESULT_FIELDS = frozenset(
    {
        "candidate_answer",
        "candidate_trace",
        "candidate_score",
        "candidate_report",
        "baseline_answer",
        "baseline_score",
        "metrics",
        "verdict",
        "judge_score",
        "faithfulness_score",
        "relevance_score",
        "answer_text",
        "report_checksum",
    }
)


class GoldBucket(StrEnum):
    """The eight reading-QA question buckets (D-01)."""

    LOCAL = "local"
    CROSS_CHAPTER = "cross_chapter"
    GLOBAL = "global"
    CAUSAL = "causal"
    CHARACTER_KNOWLEDGE = "character_knowledge"
    WORLD_RULE = "world_rule"
    NO_ANSWER = "no_answer"
    SPOILER = "spoiler"


GOLD_BUCKETS: tuple[GoldBucket, ...] = (
    GoldBucket.LOCAL,
    GoldBucket.CROSS_CHAPTER,
    GoldBucket.GLOBAL,
    GoldBucket.CAUSAL,
    GoldBucket.CHARACTER_KNOWLEDGE,
    GoldBucket.WORLD_RULE,
    GoldBucket.NO_ANSWER,
    GoldBucket.SPOILER,
)

Hash64 = str


class GoldSetError(ValueError):
    """Fail-closed gold-set error carrying a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FrozenGoldModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Hashing helpers (stable, deterministic)
# ---------------------------------------------------------------------------


def stable_json(value: object) -> str:
    """Canonical compact JSON (sorted keys, no extra whitespace)."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def chapter_content_hash(content: str) -> str:
    """Deterministic 64-hex content hash for one frozen chapter."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def slice_content_hash(content: str, start: int, end: int) -> str:
    """Content hash of a half-open Unicode code-point slice."""
    return chapter_content_hash(content[start:end])


def _strip_none(value: Any) -> Any:
    """Canonicalize: ``None`` and absent are equivalent in the gold model.

    Pydantic model dumps fill optional fields with ``null`` (e.g. a
    ``SpoilerForbiddenRef.leaf_id`` that was never authored). Stripping nulls
    keeps raw-payload and model-dump fingerprints identical.
    """
    if isinstance(value, dict):
        return {
            k: _strip_none(v) for k, v in value.items() if v is not None
        }
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def dataset_fingerprint(payload: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON; the fingerprint field is never hashed."""
    if not isinstance(payload, dict):
        raise TypeError("dataset fingerprint requires a dict payload")
    clean = {k: v for k, v in payload.items() if k != "fingerprint"}
    return hashlib.sha256(
        stable_json(_strip_none(clean)).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Contract models
# ---------------------------------------------------------------------------


class GoldChapter(FrozenGoldModel):
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(gt=0)
    content: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoldSource(FrozenGoldModel):
    novel_title: str = Field(min_length=1)
    canonicalization_version: str = Field(min_length=1)
    commit: str = Field(min_length=1)
    chapters: tuple[GoldChapter, ...]

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("chapters"), list):
            value = {**value, "chapters": tuple(value["chapters"])}
        return value


class GoldEvidenceRef(FrozenGoldModel):
    """Leaf evidence identity — chapter + offsets + content hash + snapshot."""

    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(gt=0)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _half_open_range(self) -> GoldEvidenceRef:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self

    def evidence_key(self) -> str:
        return (
            f"qp:{self.chapter_id}:{self.source_start}:{self.source_end}:"
            f"{self.content_hash}"
        )


class SourceAnswer(FrozenGoldModel):
    answer: str = Field(min_length=1, max_length=2000)
    authority: Literal["canon_fact", "probable_inference", "literary_interpretation", "user_interpretation"]
    cutoff_label: Literal["within_cutoff", "at_cutoff", "forbidden"]
    evidence: tuple[GoldEvidenceRef, ...]

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("evidence"), list):
            value = {**value, "evidence": tuple(value["evidence"])}
        return value


class SpoilerForbiddenRef(FrozenGoldModel):
    """Identity-only forbidden scope; no titles or text payloads."""

    chapter_number: int | None = Field(default=None, gt=0)
    leaf_id: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> SpoilerForbiddenRef:
        if self.chapter_number is None and self.leaf_id is None:
            raise ValueError("spoiler forbidden ref requires chapter_number or leaf_id")
        return self


class AmbiguityRubric(FrozenGoldModel):
    ambiguous: bool = False
    note: str | None = Field(default=None, max_length=400)


class CuratorRating(FrozenGoldModel):
    curator: str = Field(min_length=1, max_length=80)
    valid: bool = True
    evidence_complete: bool = True
    cutoff_ok: bool = True


class GoldSample(FrozenGoldModel):
    id: str = Field(min_length=1, max_length=128)
    bucket: GoldBucket
    query: str = Field(min_length=1, max_length=2000)
    through_chapter: int = Field(gt=0)
    full_book_authorized: bool = False
    expected_answerability: Literal["answerable", "no_answer", "spoiler_risk"]
    source_answers: tuple[SourceAnswer, ...] = ()
    spoiler_forbidden: tuple[SpoilerForbiddenRef, ...] = ()
    no_answer_rationale: str | None = Field(default=None, max_length=400)
    ambiguity: AmbiguityRubric = AmbiguityRubric()
    curator_ratings: tuple[CuratorRating, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = {}
            for key in ("source_answers", "spoiler_forbidden", "curator_ratings"):
                if isinstance(value.get(key), list):
                    updates[key] = tuple(value[key])
            if updates:
                value = {**value, **updates}
        return value

    @model_validator(mode="after")
    def _bucket_rules(self) -> GoldSample:
        if self.bucket == GoldBucket.NO_ANSWER:
            if self.expected_answerability != "no_answer":
                raise ValueError("no_answer bucket requires no_answer answerability")
            if self.source_answers:
                raise ValueError("no_answer bucket must not carry source answers")
            if not self.no_answer_rationale:
                raise ValueError("no_answer bucket requires no_answer_rationale")
        elif self.bucket == GoldBucket.SPOILER:
            if self.expected_answerability != "spoiler_risk":
                raise ValueError("spoiler bucket requires spoiler_risk answerability")
            if self.source_answers:
                raise ValueError("spoiler bucket must not carry source answers")
            if not self.spoiler_forbidden:
                raise ValueError("spoiler bucket requires non-empty spoiler_forbidden")
        else:
            if self.expected_answerability != "answerable":
                raise ValueError(f"{self.bucket} requires answerable answerability")
            if not self.source_answers:
                raise ValueError(f"{self.bucket} requires non-empty source_answers")
        return self


class ReadingQAGoldSet(FrozenGoldModel):
    schema_version: str = GOLD_SCHEMA_VERSION
    dataset_version: str = DEFAULT_DATASET_VERSION
    partition: str = PARTITION_FROZEN
    dataset: str = "single_book"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: GoldSource
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    curators: tuple[str, ...] = ()
    samples: tuple[GoldSample, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = {}
            for key in ("curators", "samples"):
                if isinstance(value.get(key), list):
                    updates[key] = tuple(value[key])
            if updates:
                value = {**value, **updates}
        return value

    @model_validator(mode="after")
    def _dataset_integrity(self) -> ReadingQAGoldSet:
        # Chapter uniqueness + content integrity
        seen: set[int] = set()
        for ch in self.source.chapters:
            if ch.chapter_number in seen:
                raise ValueError(f"duplicate chapter_number {ch.chapter_number}")
            seen.add(ch.chapter_number)
            if ch.content_hash != chapter_content_hash(ch.content):
                raise ValueError(
                    f"chapter {ch.chapter_number} content_hash mismatch"
                )

        # Eight buckets must all be present
        present = {s.bucket for s in self.samples}
        missing = [b.value for b in GOLD_BUCKETS if b not in present]
        if missing:
            raise ValueError(f"missing buckets: {missing}")

        # Unique sample ids
        ids = [s.id for s in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate sample id")

        # Per-sample gold evidence validation (re-slice against frozen source)
        max_chapter = max((c.chapter_number for c in self.source.chapters), default=0)
        for sample in self.samples:
            if sample.through_chapter > max_chapter:
                raise ValueError(
                    f"sample {sample.id} through_chapter {sample.through_chapter} "
                    f"exceeds book length {max_chapter} (future metadata)"
                )
            for sa in sample.source_answers:
                for ref in sa.evidence:
                    if ref.source_snapshot_hash != self.source_snapshot_hash:
                        raise ValueError(
                            f"sample {sample.id} gold evidence cross-snapshot "
                            f"(owner/novel/version/snapshot boundary)"
                        )
                    if (
                        not sample.full_book_authorized
                        and ref.chapter_number > sample.through_chapter
                    ):
                        raise ValueError(
                            f"sample {sample.id} gold evidence beyond cutoff "
                            f"({ref.chapter_number} > {sample.through_chapter})"
                        )
                    chapter = next(
                        (
                            c
                            for c in self.source.chapters
                            if c.chapter_id == ref.chapter_id
                        ),
                        None,
                    )
                    if chapter is None:
                        raise ValueError(
                            f"sample {sample.id} gold evidence foreign chapter "
                            f"{ref.chapter_id}"
                        )
                    if chapter.chapter_number != ref.chapter_number:
                        raise ValueError(
                            f"sample {sample.id} gold evidence chapter_number mismatch"
                        )
                    content = chapter.content
                    if (
                        ref.source_start < 0
                        or ref.source_end > len(content)
                        or ref.source_end <= ref.source_start
                    ):
                        raise ValueError(
                            f"sample {sample.id} gold evidence offsets invalid"
                        )
                    if (
                        slice_content_hash(content, ref.source_start, ref.source_end)
                        != ref.content_hash
                    ):
                        raise ValueError(
                            f"sample {sample.id} gold evidence content_hash mismatch "
                            f"after re-slice"
                        )
        return self

    def bucket_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {b.value: 0 for b in GOLD_BUCKETS}
        for sample in self.samples:
            counts[sample.bucket.value] += 1
        return counts

    def chapter_by_number(self, chapter_number: int) -> GoldChapter | None:
        return next(
            (
                c
                for c in self.source.chapters
                if c.chapter_number == chapter_number
            ),
            None,
        )


# ---------------------------------------------------------------------------
# Curator agreement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CuratorAgreement:
    """Deterministic curator agreement over per-sample ratings."""

    overall: float  # fraction of rated samples that are unanimous
    rated_samples: int  # samples carrying >= 2 curator ratings
    unanimous_samples: int
    per_sample: dict[str, bool]
    per_sample_rating_count: dict[str, int]

    @property
    def is_unanimous(self) -> bool:
        return self.rated_samples > 0 and self.overall == 1.0


def _rating_tuple(rating: CuratorRating) -> tuple[bool, bool, bool]:
    return (rating.valid, rating.evidence_complete, rating.cutoff_ok)


def curator_agreement(gold_set: ReadingQAGoldSet) -> CuratorAgreement:
    """Unanimous-agreement ratio; disagreement can never reach 1.0."""
    per_sample: dict[str, bool] = {}
    per_sample_rating_count: dict[str, int] = {}
    for sample in gold_set.samples:
        ratings = sample.curator_ratings
        per_sample_rating_count[sample.id] = len(ratings)
        if len(ratings) < 2:
            per_sample[sample.id] = False
            continue
        per_sample[sample.id] = len({_rating_tuple(r) for r in ratings}) == 1
    rated = [
        sid for sid, count in per_sample_rating_count.items() if count >= 2
    ]
    unanimous = [sid for sid in rated if per_sample[sid]]
    overall = len(unanimous) / len(rated) if rated else 0.0
    return CuratorAgreement(
        overall=overall,
        rated_samples=len(rated),
        unanimous_samples=len(unanimous),
        per_sample=per_sample,
        per_sample_rating_count=per_sample_rating_count,
    )


# ---------------------------------------------------------------------------
# Loader / freeze
# ---------------------------------------------------------------------------


def reject_gold_result_fields(payload: dict[str, Any]) -> None:
    """Fail closed if the frozen payload carries candidate-result fields."""
    found = sorted(FORBIDDEN_GOLD_RESULT_FIELDS.intersection(payload.keys()))
    if found:
        raise GoldSetError(
            "result_fields_forbidden",
            f"result-derived fields forbidden in gold set: {found}",
        )


def freeze_gold_set(
    payload: dict[str, Any],
    *,
    require_frozen: bool = True,
    require_agreement: bool = True,
) -> ReadingQAGoldSet:
    """Validate, verify fingerprint/agreement and return the frozen model."""
    reject_gold_result_fields(payload)
    try:
        gold_set = ReadingQAGoldSet.model_validate(payload)
    except Exception as exc:
        raise GoldSetError("invalid_gold_set", str(exc)) from exc

    computed = dataset_fingerprint(payload)
    stored = payload.get("fingerprint")
    if require_frozen:
        if stored is None:
            raise GoldSetError(
                "fingerprint_missing",
                "frozen gold set requires a stored fingerprint",
            )
        if stored != computed:
            raise GoldSetError(
                "fingerprint_mismatch",
                f"stored fingerprint {stored!r} != recomputed {computed!r}",
            )
    agreement = curator_agreement(gold_set)
    if require_agreement and not agreement.is_unanimous:
        disagree = [
            sid
            for sid, ok in agreement.per_sample.items()
            if not ok and agreement.per_sample_rating_count[sid] >= 2
        ]
        raise GoldSetError(
            "curator_disagreement",
            f"curator agreement {agreement.overall:.3f} < 1.0; "
            f"disagreeing samples: {sorted(disagree)}",
        )
    return gold_set


def load_gold_set(
    path: Path | str,
    *,
    require_frozen: bool = True,
    require_agreement: bool = True,
) -> ReadingQAGoldSet:
    """Load a frozen reading-QA gold set from a JSON file."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoldSetError("gold_set_missing", f"gold set not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise GoldSetError("gold_set_invalid_json", f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise GoldSetError("gold_set_invalid_json", "gold set root must be object")
    return freeze_gold_set(
        raw,
        require_frozen=require_frozen,
        require_agreement=require_agreement,
    )


def gold_set_has_forbidden_capability() -> bool:
    """Zero provider / promotion / baseline capability by construction."""
    return False


def gold_set_has_promotion_capability() -> bool:
    return False
