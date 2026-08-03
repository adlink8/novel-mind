"""Source / cutoff / authority scoring rubric for the reading QA gold set.

Phase 29-01 / REQ-QA-01; decisions D-02, D-04, D-05 from 29-CONTEXT.md.

The rubric scores a candidate's cited evidence against the frozen gold source
and the sample's cutoff, then applies fail-closed gates:

- source: evidence must re-slice against the frozen snapshot; stale hashes,
  foreign chapters and cross-snapshot refs reject (D-07-like).
- cutoff: evidence must stay at/before ``through_chapter`` unless the whole
  book is explicitly authorized (D-04 identical source/cutoff).
- authority: labels must come from the four canonical epistemic labels.
- leakage / owner / spoiler / lineage violations always BLOCK, never pass.

The only allowed verdicts are ``qualified_candidate`` and ``blocked`` (D-05).
``blocked`` is a legal, first-class outcome; there is no promotion vocabulary.

Pure module: no database, no network, no provider calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from app.services.qualification.gold_set import (
    AUTHORITY_LABELS,
    GOLD_BUCKETS,
    GoldBucket,
    GoldEvidenceRef,
    GoldSample,
    ReadingQAGoldSet,
    curator_agreement,
    dataset_fingerprint,
    slice_content_hash,
)


class RubricVerdict(StrEnum):
    """Two-value verdict only (D-05)."""

    QUALIFIED_CANDIDATE = "qualified_candidate"
    BLOCKED = "blocked"


# Stable machine codes for the fail-closed gates.
CODE_FINGERPRINT_MISMATCH = "fingerprint_mismatch"
CODE_CURATOR_DISAGREEMENT = "curator_disagreement"
CODE_MISSING_BUCKET = "missing_bucket"
CODE_FUTURE_CUTOFF = "future_cutoff"
CODE_MISSING_LINEAGE = "missing_lineage"
CODE_CROSS_SNAPSHOT = "cross_snapshot"
CODE_FOREIGN_CHAPTER = "foreign_chapter"
CODE_CHAPTER_NUMBER_MISMATCH = "chapter_number_mismatch"
CODE_INVALID_OFFSETS = "invalid_offsets"
CODE_EVIDENCE_CONTENT_MISMATCH = "evidence_content_mismatch"
CODE_GOLD_BEYOND_CUTOFF = "gold_beyond_cutoff"
CODE_BEYOND_CUTOFF = "beyond_cutoff"
CODE_SPOILER_LEAK = "spoiler_leak"
CODE_NO_ANSWER_HALLUCINATION = "no_answer_hallucination"
CODE_UNCITED_ASSERTION = "uncited_assertion"
CODE_CITATION_OUTSIDE_GOLD = "citation_outside_gold"
CODE_CONTENT_LEAK = "content_leak_beyond_cutoff"
CODE_CROSS_OWNER = "cross_owner"
CODE_CROSS_NOVEL = "cross_novel"
CODE_CROSS_VERSION = "cross_version"
CODE_EMPTY_ANSWER = "empty_answer"
CODE_AUTHORITY_UNKNOWN = "authority_unknown"
CODE_ILLEGAL_VERDICT = "illegal_verdict"

BORROW_MIN_LEN = 6


@dataclass(frozen=True)
class Violation:
    """One fail-closed gate failure with a stable machine code."""

    code: str
    sample_id: str | None
    detail: str


@dataclass(frozen=True)
class RubricResult:
    verdict: RubricVerdict
    violations: tuple[Violation, ...]
    fingerprint: str
    agreement: float
    bucket_counts: dict[str, int]
    audited_samples: int

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(sorted({v.code for v in self.violations}))


# ---------------------------------------------------------------------------
# Evidence audit helpers
# ---------------------------------------------------------------------------


def _chapter_for(gold_set: ReadingQAGoldSet, ref: GoldEvidenceRef):
    return next(
        (c for c in gold_set.source.chapters if c.chapter_id == ref.chapter_id),
        None,
    )


def audit_evidence_ref(
    gold_set: ReadingQAGoldSet,
    sample: GoldSample,
    ref: GoldEvidenceRef,
) -> list[Violation]:
    """Fail-closed evidence audit: source, cutoff and lineage of one ref."""
    violations: list[Violation] = []
    if ref.source_snapshot_hash != gold_set.source_snapshot_hash:
        violations.append(
            Violation(
                CODE_CROSS_SNAPSHOT,
                sample.id,
                "evidence ref escapes the frozen snapshot lineage "
                "(owner/novel/version/snapshot boundary)",
            )
        )
    chapter = _chapter_for(gold_set, ref)
    if chapter is None:
        violations.append(
            Violation(
                CODE_FOREIGN_CHAPTER,
                sample.id,
                f"chapter {ref.chapter_id} absent from the frozen snapshot",
            )
        )
        return violations
    if chapter.chapter_number != ref.chapter_number:
        violations.append(
            Violation(
                CODE_CHAPTER_NUMBER_MISMATCH,
                sample.id,
                f"chapter {ref.chapter_id} number {chapter.chapter_number} != "
                f"ref {ref.chapter_number}",
            )
        )
    content = chapter.content
    if (
        ref.source_start < 0
        or ref.source_end > len(content)
        or ref.source_end <= ref.source_start
    ):
        violations.append(
            Violation(
                CODE_INVALID_OFFSETS,
                sample.id,
                f"offsets [{ref.source_start},{ref.source_end}) invalid inside "
                f"chapter {ref.chapter_number}",
            )
        )
    else:
        actual = slice_content_hash(content, ref.source_start, ref.source_end)
        if actual != ref.content_hash:
            violations.append(
                Violation(
                    CODE_EVIDENCE_CONTENT_MISMATCH,
                    sample.id,
                    f"evidence content hash {actual[:12]} != frozen "
                    f"{ref.content_hash[:12]} after re-slice (source mutation)",
                )
            )
    if not sample.full_book_authorized and ref.chapter_number > sample.through_chapter:
        violations.append(
            Violation(
                CODE_BEYOND_CUTOFF,
                sample.id,
                f"evidence chapter {ref.chapter_number} exceeds cutoff "
                f"{sample.through_chapter}",
            )
        )
    return violations


def _gold_evidence_keys(sample: GoldSample) -> set[str]:
    return {
        ref.evidence_key()
        for answer in sample.source_answers
        for ref in answer.evidence
    }


def find_beyond_cutoff_borrow(
    answer: str,
    gold_set: ReadingQAGoldSet,
    sample: GoldSample,
    min_len: int = BORROW_MIN_LEN,
) -> list[tuple[int, str]]:
    """Return (chapter_number, window) for text borrowed from beyond-cutoff chapters.

    A window of ``answer`` that also appears verbatim inside an allowed
    within-cutoff gold excerpt is not treated as a leak.
    """
    if sample.full_book_authorized or not answer:
        return []
    answer = answer.strip()
    cutoff = sample.through_chapter
    allowed: set[str] = set()
    for sa in sample.source_answers:
        for ref in sa.evidence:
            chapter = _chapter_for(gold_set, ref)
            if chapter is None or chapter.chapter_number > cutoff:
                continue
            allowed.add(chapter.content[ref.source_start : ref.source_end])
    findings: list[tuple[int, str]] = []
    for chapter in gold_set.source.chapters:
        if chapter.chapter_number <= cutoff:
            continue
        found: str | None = None
        for i in range(0, len(answer) - min_len + 1):
            window = answer[i : i + min_len]
            if window not in chapter.content:
                continue
            if any(window in allowed_text for allowed_text in allowed):
                continue
            found = window
            break
        if found is not None:
            findings.append((chapter.chapter_number, found))
    return findings


# ---------------------------------------------------------------------------
# Dataset-level audit
# ---------------------------------------------------------------------------


def audit_dataset(gold_set: ReadingQAGoldSet) -> list[Violation]:
    """Fingerprint, agreement, lineage, bucket completeness and gold evidence."""
    violations: list[Violation] = []
    # fingerprint
    payload = gold_set.model_dump(mode="json")
    computed = dataset_fingerprint(payload)
    stored = gold_set.fingerprint
    if stored is None or stored != computed:
        violations.append(
            Violation(
                CODE_FINGERPRINT_MISMATCH,
                None,
                f"dataset fingerprint drift: stored {stored!r} != {computed[:12]}...",
            )
        )
    # curator agreement
    agreement = curator_agreement(gold_set)
    if not agreement.is_unanimous:
        violations.append(
            Violation(
                CODE_CURATOR_DISAGREEMENT,
                None,
                f"curator agreement {agreement.overall:.3f} < 1.0",
            )
        )
    # mandatory lineage
    if not (gold_set.owner_id and gold_set.novel_id and gold_set.version_id):
        violations.append(
            Violation(CODE_MISSING_LINEAGE, None, "owner/novel/version lineage missing")
        )
    # bucket completeness (defense in depth; model already enforces on load)
    counts = gold_set.bucket_counts()
    for bucket in GOLD_BUCKETS:
        if counts[bucket.value] < 1:
            violations.append(
                Violation(CODE_MISSING_BUCKET, None, f"bucket {bucket.value} empty")
            )
    # future metadata: cutoff beyond book length
    max_chapter = max((c.chapter_number for c in gold_set.source.chapters), default=0)
    for sample in gold_set.samples:
        if sample.through_chapter > max_chapter:
            violations.append(
                Violation(
                    CODE_FUTURE_CUTOFF,
                    sample.id,
                    f"through_chapter {sample.through_chapter} exceeds book length "
                    f"{max_chapter}",
                )
            )
    # gold evidence audit
    for sample in gold_set.samples:
        for answer in sample.source_answers:
            if answer.authority not in AUTHORITY_LABELS:
                violations.append(
                    Violation(
                        CODE_AUTHORITY_UNKNOWN,
                        sample.id,
                        f"unknown authority label {answer.authority!r}",
                    )
                )
            for ref in answer.evidence:
                violations.extend(audit_evidence_ref(gold_set, sample, ref))
                if (
                    not sample.full_book_authorized
                    and ref.chapter_number > sample.through_chapter
                ):
                    violations.append(
                        Violation(
                            CODE_GOLD_BEYOND_CUTOFF,
                            sample.id,
                            "gold evidence beyond cutoff in frozen set",
                        )
                    )
    return violations


# ---------------------------------------------------------------------------
# Candidate-level audit
# ---------------------------------------------------------------------------


def audit_candidate_answer(
    gold_set: ReadingQAGoldSet,
    sample: GoldSample,
    *,
    answer: str,
    cited_evidence: Iterable[GoldEvidenceRef | dict[str, Any]] = (),
    abstained: bool = False,
    context: dict[str, Any] | None = None,
) -> list[Violation]:
    """Audit one candidate answer against the frozen gold sample.

    Leakage, owner, spoiler and lineage violations always block (D-04/D-05).
    """
    violations: list[Violation] = []
    refs = [to_evidence_ref(r) for r in cited_evidence]

    if answer is None:
        answer = ""
    answer = str(answer).strip()

    # authority is evaluated on gold source answers; a candidate relabel is a
    # violation when a supplied authority is non-canonical.
    if context and context.get("authority") not in (None, *AUTHORITY_LABELS):
        violations.append(
            Violation(
                CODE_AUTHORITY_UNKNOWN,
                sample.id,
                f"candidate authority {context.get('authority')!r} not canonical",
            )
        )

    # lineage gates: owner / novel / version / snapshot
    if context is not None:
        if context.get("owner_id") not in (None, gold_set.owner_id):
            violations.append(
                Violation(CODE_CROSS_OWNER, sample.id, "candidate owner mismatch")
            )
        if context.get("novel_id") not in (None, gold_set.novel_id):
            violations.append(
                Violation(CODE_CROSS_NOVEL, sample.id, "candidate novel mismatch")
            )
        if context.get("version_id") not in (None, gold_set.version_id):
            violations.append(
                Violation(CODE_CROSS_VERSION, sample.id, "candidate version mismatch")
            )

    # evidence audit for every cited ref
    forbidden = {
        f.chapter_number
        for f in sample.spoiler_forbidden
        if f.chapter_number is not None
    }
    for ref in refs:
        violations.extend(audit_evidence_ref(gold_set, sample, ref))
        if sample.bucket == GoldBucket.SPOILER:
            if (
                not sample.full_book_authorized
                and ref.chapter_number > sample.through_chapter
            ):
                violations.append(
                    Violation(
                        CODE_SPOILER_LEAK,
                        sample.id,
                        f"spoiler evidence chapter {ref.chapter_number} beyond cutoff",
                    )
                )
            if ref.chapter_number in forbidden:
                violations.append(
                    Violation(
                        CODE_SPOILER_LEAK,
                        sample.id,
                        f"spoiler evidence touches forbidden chapter {ref.chapter_number}",
                    )
                )
        elif ref.chapter_number in forbidden:
            violations.append(
                Violation(
                    CODE_SPOILER_LEAK,
                    sample.id,
                    f"answerable evidence touches forbidden chapter {ref.chapter_number}",
                )
            )

    # citation correctness for answerable buckets: cited key must be gold
    if sample.expected_answerability == "answerable":
        gold_keys = _gold_evidence_keys(sample)
        for ref in refs:
            if ref.evidence_key() not in gold_keys:
                violations.append(
                    Violation(
                        CODE_CITATION_OUTSIDE_GOLD,
                        sample.id,
                        f"cited evidence {ref.evidence_key()[:24]}... outside "
                        f"gold allowlist",
                    )
                )

    # no-answer gate: answering a no_answer sample is a hallucination
    if sample.expected_answerability == "no_answer":
        if not abstained:
            violations.append(
                Violation(
                    CODE_NO_ANSWER_HALLUCINATION,
                    sample.id,
                    "candidate answered a no_answer sample instead of abstaining",
                )
            )
    elif sample.expected_answerability == "answerable" and not abstained:
        if not answer:
            violations.append(
                Violation(CODE_EMPTY_ANSWER, sample.id, "answerable answer is empty")
            )
        if not refs:
            violations.append(
                Violation(
                    CODE_UNCITED_ASSERTION,
                    sample.id,
                    "answerable answer carries no cited evidence",
                )
            )

    # spoiler gate: content borrowed from forbidden / beyond-cutoff chapters
    leaks = find_beyond_cutoff_borrow(answer, gold_set, sample)
    if sample.bucket == GoldBucket.SPOILER:
        for chapter_number, window in leaks:
            violations.append(
                Violation(
                    CODE_SPOILER_LEAK,
                    sample.id,
                    f"answer borrows chapter {chapter_number} content: {window!r}",
                )
            )
    else:
        for chapter_number, window in leaks:
            violations.append(
                Violation(
                    CODE_CONTENT_LEAK,
                    sample.id,
                    f"answer borrows beyond-cutoff chapter {chapter_number} "
                    f"content: {window!r}",
                )
            )
    return violations


def to_evidence_ref(value: GoldEvidenceRef | dict[str, Any]) -> GoldEvidenceRef:
    if isinstance(value, GoldEvidenceRef):
        return value
    if isinstance(value, dict):
        return GoldEvidenceRef.model_validate(value)
    raise TypeError(f"cannot coerce {type(value).__name__} to GoldEvidenceRef")


# ---------------------------------------------------------------------------
# Qualification entry point
# ---------------------------------------------------------------------------


def evaluate_qualification(
    gold_set: ReadingQAGoldSet,
    *,
    candidate_answers: dict[str, dict[str, Any]] | None = None,
    context_by_sample: dict[str, dict[str, Any]] | None = None,
) -> RubricResult:
    """Deterministic two-verdict qualification of the gold set (+ candidates)."""
    violations = audit_dataset(gold_set)
    candidate_answers = candidate_answers or {}
    context_by_sample = context_by_sample or {}
    for sample in gold_set.samples:
        candidate = candidate_answers.get(sample.id)
        if candidate is None:
            continue
        violations.extend(
            audit_candidate_answer(
                gold_set,
                sample,
                answer=candidate.get("answer", ""),
                cited_evidence=candidate.get("evidence", ()),
                abstained=bool(candidate.get("abstained", False)),
                context=context_by_sample.get(sample.id),
            )
        )
    verdict = (
        RubricVerdict.BLOCKED
        if violations
        else RubricVerdict.QUALIFIED_CANDIDATE
    )
    agreement = curator_agreement(gold_set)
    return RubricResult(
        verdict=verdict,
        violations=tuple(violations),
        fingerprint=gold_set.fingerprint or dataset_fingerprint(
            gold_set.model_dump(mode="json")
        ),
        agreement=agreement.overall,
        bucket_counts=gold_set.bucket_counts(),
        audited_samples=len(gold_set.samples),
    )
