"""Phase 26-03 manifest integration tests (REQ-QP-03, D-07/D-08/D-09/D-12/D-14).

Covers the full frozen-manifest pipeline: parser -> fusion -> leaf EvidenceRef
materialization (Unicode offsets + content hash) -> immutable Frozen Manifest
freeze -> cited-answer gate. Every text / hash / offset / owner / spoiler /
version mutation fails closed, replay is by checksum, and an evidence-less
answer abstains with omitted / fallback records.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.schemas.reader_chat import ReaderAnswerEnvelope, validate_answer_against_manifest
from app.services.queryplan.adapters import (
    ChapterRecord,
    DimensionResult,
    SourceSnapshot,
    chapter_content_hash,
)
from app.services.queryplan.evidence import (
    EvidenceError,
    FrozenManifest,
    freeze_manifest,
    materialize_evidence_ref,
    verify_manifest,
)
from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    CutoffMode,
    EvidenceRef,
    FallbackStage,
    QueryDimension,
    QueryPlan,
)
from app.services.queryplan.service import QueryPlanService

pytestmark = pytest.mark.integration

HEX_SNAPSHOT = "c" * 64
HEX_OTHER = "d" * 64

# Astral code point (🀄) included to prove Unicode code-point offsets.
CHAPTER_1_TEXT = "林安走进竹林，剑客随后现身。🀄 她低声问：你是谁？"
CHAPTER_2_TEXT = "第二章：剑客没有回答。林安握紧剑柄。"
CHAPTER_3_TEXT = "第三章：两人并肩走出竹林。"


def make_chapter(
    chapter_id: int, chapter_number: int, content: str
) -> ChapterRecord:
    return ChapterRecord(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        content=content,
        content_hash=chapter_content_hash(content),
    )


def make_source(*, snapshot_hash: str = HEX_SNAPSHOT) -> SourceSnapshot:
    return SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=1,
        snapshot_hash=snapshot_hash,
        chapters=(
            make_chapter(1, 1, CHAPTER_1_TEXT),
            make_chapter(2, 2, CHAPTER_2_TEXT),
            make_chapter(3, 3, CHAPTER_3_TEXT),
        ),
    )


def make_plan(
    *,
    through_chapter: int = 3,
    whole_book: bool = False,
    full_book_authorized: bool = False,
) -> QueryPlan:
    result = parse_query_plan(
        {
            "intent": "reader",
            "owner_id": 1,
            "novel_id": 1,
            "version_id": 1,
            "question_text": "林安走进哪里？",
            "reading_progress": {
                "through_chapter": through_chapter,
                "snapshot_hash": HEX_SNAPSHOT,
                "full_book_authorized": full_book_authorized,
            },
            "whole_book": whole_book,
            "source": "reader_chat",
        }
    )
    assert isinstance(result, QueryPlan), result
    return result


def leaf_ref(
    *,
    chapter_id: int = 1,
    chapter_number: int = 1,
    content: str = CHAPTER_1_TEXT,
    start: int = 0,
    end: int | None = None,
    snapshot_hash: str = HEX_SNAPSHOT,
) -> EvidenceRef:
    if end is None:
        end = len(content[:10])
    excerpt = content[start:end]
    return EvidenceRef(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_start=start,
        source_end=end,
        content_hash=chapter_content_hash(excerpt),
        source_snapshot_hash=snapshot_hash,
    )


def make_available(refs: tuple[EvidenceRef, ...]) -> DimensionResult:
    return DimensionResult(
        dimension=QueryDimension.RAW_TEXT,
        status=AvailabilityStatus.AVAILABLE,
        reason="reader_ok",
        provenance="exact_reader_v1",
        stage=FallbackStage.EXACT_READER,
        refs=refs,
    )


def make_unavailable(
    dimension: QueryDimension = QueryDimension.WORLD_RULES,
) -> DimensionResult:
    return DimensionResult(
        dimension=dimension,
        status=AvailabilityStatus.UNAVAILABLE,
        reason="dimension_unavailable",
        provenance="deterministic_contract_v1",
        stage=FallbackStage.STABLE_UNAVAILABLE,
    )


def legal_producer():
    async def produce(_manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        keys = sorted(_manifest.allowed_evidence_ids())
        if not keys:
            return ReaderAnswerEnvelope.model_validate(
                {
                    "schema_version": "reader-answer.v1",
                    "answer_blocks": [],
                    "clarifying_question": "证据不足，无法作答。",
                    "uncertainty": None,
                    "suggestion_candidates": [],
                }
            )
        return ReaderAnswerEnvelope.model_validate(
            {
                "schema_version": "reader-answer.v1",
                "answer_blocks": [
                    {
                        "block_id": "b1",
                        "text": "林安走进竹林。",
                        "evidence_refs": [keys[0]],
                    }
                ],
                "clarifying_question": None,
                "uncertainty": None,
                "suggestion_candidates": [],
            }
        )

    return produce


async def run_pipeline(
    *,
    plan: QueryPlan | None = None,
    source: SourceSnapshot | None = None,
    refs: tuple[EvidenceRef, ...] = (),
    producer=None,
):
    plan = plan or make_plan()
    source = source or make_source()
    results = [make_available(refs)] if refs else [make_unavailable()]
    answer = await QueryPlanService().execute(
        plan,
        source,
        dimension_results=results,
        answer_producer=producer or legal_producer(),
    )
    return plan, source, answer


# ---------------------------------------------------------------------------
# Freeze / replay / allowlist (D-07/D-08)
# ---------------------------------------------------------------------------


async def test_manifest_freeze_replay_checksum_and_allowlist():
    ref = leaf_ref()
    _, _, answer = await run_pipeline(refs=(ref,))
    manifest = answer.manifest

    assert manifest.owner_id == 1
    assert manifest.novel_id == 1
    assert manifest.version_id == 1
    assert manifest.through_chapter == 3
    assert manifest.cutoff_mode == CutoffMode.READING_PROGRESS.value
    assert manifest.full_book_authorized is False
    assert manifest.snapshot_hash == HEX_SNAPSHOT
    assert manifest.plan_trace_id == answer.plan.trace.trace_id

    # Content-addressed: manifest id equals the canonical checksum.
    assert len(manifest.manifest_checksum) == 64
    assert manifest.manifest_id == manifest.manifest_checksum

    # Only materialized leaf/raw evidence is allowlisted.
    allowed = manifest.allowed_evidence_ids()
    assert len(allowed) == 1
    key = sorted(allowed)[0]
    assert key.startswith("qp:1:0:")

    entry = manifest.evidence[0]
    assert entry.excerpt == CHAPTER_1_TEXT[:10]
    assert entry.content_hash == chapter_content_hash(entry.excerpt)

    # Replay: rehydrate from the canonical payload and recompute the checksum.
    from app.services.queryplan.evidence import manifest_checksum

    assert manifest_checksum(manifest.canonical_payload()) == manifest.manifest_checksum

    verify_manifest(manifest)
    assert answer.envelope.answer_blocks[0].evidence_refs == [key]
    assert answer.abstained is False


async def test_deterministic_replay_same_inputs_same_checksum():
    # Same plan object (same trace lineage) + same snapshot -> same content address.
    plan = make_plan()
    ref = leaf_ref()
    _, _, first = await run_pipeline(plan=plan, refs=(ref,))
    _, _, second = await run_pipeline(plan=plan, refs=(ref,))
    assert first.manifest.manifest_checksum == second.manifest.manifest_checksum
    assert (
        first.manifest.allowed_evidence_ids()
        == second.manifest.allowed_evidence_ids()
    )


# ---------------------------------------------------------------------------
# Stale / drift / scope fail-closed materialization (D-07/D-12)
# ---------------------------------------------------------------------------


def test_stale_content_hash_rejected():
    ref = leaf_ref().model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(
            ref, source=make_source(), through_chapter=3
        )
    assert exc.value.code == "stale_content_hash"


def test_shifted_offset_rejected():
    # Claimed offsets no longer match the hash the ref was built with.
    ref = leaf_ref().model_copy(update={"source_start": 1})
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(
            ref, source=make_source(), through_chapter=3
        )
    assert exc.value.code == "stale_content_hash"


def test_out_of_bounds_offsets_rejected():
    ref = leaf_ref(start=0, end=len(CHAPTER_1_TEXT) + 5)
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(
            ref, source=make_source(), through_chapter=3
        )
    assert exc.value.code == "invalid_offsets"


def test_stale_snapshot_lineage_rejected():
    ref = leaf_ref(snapshot_hash=HEX_OTHER)
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(
            ref, source=make_source(), through_chapter=3
        )
    assert exc.value.code == "stale_snapshot_lineage"


def test_chapter_number_mismatch_rejected():
    ref = leaf_ref(chapter_number=2)
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(
            ref, source=make_source(), through_chapter=3
        )
    assert exc.value.code == "chapter_number_mismatch"


def test_beyond_cutoff_rejected():
    ref = leaf_ref(
        chapter_id=3,
        chapter_number=3,
        content=CHAPTER_3_TEXT,
        end=len(CHAPTER_3_TEXT[:10]),
    )
    with pytest.raises(EvidenceError) as exc:
        materialize_evidence_ref(
            ref, source=make_source(), through_chapter=2
        )
    assert exc.value.code == "beyond_cutoff"


def test_whole_book_keeps_lineage_and_bypasses_cutoff():
    plan = make_plan(through_chapter=2, whole_book=True, full_book_authorized=True)
    ref = leaf_ref(
        chapter_id=3,
        chapter_number=3,
        content=CHAPTER_3_TEXT,
        end=len(CHAPTER_3_TEXT[:10]),
    )
    entry = materialize_evidence_ref(
        ref,
        source=make_source(),
        through_chapter=3,
        cutoff_mode=CutoffMode.WHOLE_BOOK,
    )
    assert entry.chapter_number == 3
    manifest = freeze_manifest(
        plan=plan, source=make_source(), evidence=(entry,), omitted=()
    )
    assert manifest.cutoff_mode == CutoffMode.WHOLE_BOOK.value
    assert manifest.full_book_authorized is True


def test_unicode_astral_offsets_are_code_point_based():
    needle = "🀄"
    start = CHAPTER_1_TEXT.find(needle)
    end = start + len(needle)
    ref = leaf_ref(start=start, end=end)
    entry = materialize_evidence_ref(ref, source=make_source(), through_chapter=3)
    assert entry.excerpt == needle


# ---------------------------------------------------------------------------
# Immutability and manifest mutation fail-closed (D-08/D-14)
# ---------------------------------------------------------------------------


def test_frozen_manifest_is_immutable():
    plan = make_plan()
    source = make_source()
    entry = materialize_evidence_ref(
        leaf_ref(), source=source, through_chapter=3
    )
    manifest = freeze_manifest(
        plan=plan, source=source, evidence=(entry,), omitted=()
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        manifest.manifest_checksum = "0" * 64  # type: ignore[misc]


@pytest.mark.parametrize(
    "mutation",
    [
        "excerpt_text",
        "content_hash",
        "source_start",
        "source_end",
        "owner_id",
        "version_id",
        "cutoff_mode",
        "snapshot_hash",
    ],
)
def test_manifest_mutation_fails_closed(mutation: str):
    plan = make_plan()
    source = make_source()
    entry = materialize_evidence_ref(
        leaf_ref(), source=source, through_chapter=3
    )
    manifest = freeze_manifest(
        plan=plan, source=source, evidence=(entry,), omitted=()
    )
    tampered = _tamper(manifest, entry, mutation)
    with pytest.raises(EvidenceError) as exc:
        verify_manifest(tampered)
    assert exc.value.code == "manifest_mutated"


def _tamper(manifest: FrozenManifest, entry, mutation: str) -> FrozenManifest:
    if mutation == "excerpt_text":
        entry = dataclasses.replace(entry, excerpt="篡改后的文本")
    elif mutation == "content_hash":
        entry = dataclasses.replace(entry, content_hash="1" * 64)
    elif mutation == "source_start":
        entry = dataclasses.replace(entry, source_start=1)
    elif mutation == "source_end":
        entry = dataclasses.replace(entry, source_end=8)
    elif mutation == "owner_id":
        return dataclasses.replace(manifest, owner_id=999)
    elif mutation == "version_id":
        return dataclasses.replace(manifest, version_id=999)
    elif mutation == "cutoff_mode":
        return dataclasses.replace(manifest, cutoff_mode="whole_book")
    elif mutation == "snapshot_hash":
        return dataclasses.replace(manifest, snapshot_hash=HEX_OTHER)
    else:  # pragma: no cover
        raise AssertionError(mutation)
    return dataclasses.replace(manifest, evidence=(entry,))


# ---------------------------------------------------------------------------
# Abstention + omitted records (D-09)
# ---------------------------------------------------------------------------


async def test_no_evidence_abstains_and_records_omitted():
    _, _, answer = await run_pipeline()
    assert answer.manifest.evidence == ()
    assert answer.manifest.allowed_evidence_ids() == set()
    assert answer.envelope.answer_blocks == []
    assert answer.envelope.clarifying_question
    assert answer.abstained is True
    assert len(answer.manifest.omitted) > 0
    kinds = {entry.kind for entry in answer.manifest.omitted}
    assert "dimension" in kinds


async def test_uncited_factual_with_no_evidence_rejected():
    async def bad_producer(_manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        return ReaderAnswerEnvelope.model_validate(
            {
                "schema_version": "reader-answer.v1",
                "answer_blocks": [
                    {
                        "block_id": "b1",
                        "text": "林安走进竹林。",
                        "evidence_refs": ["summary:ch1"],
                    }
                ],
                "clarifying_question": None,
                "uncertainty": None,
                "suggestion_candidates": [],
            }
        )

    plan = make_plan()
    source = make_source()
    service = QueryPlanService()
    with pytest.raises(ValueError):
        await service.execute(
            plan,
            source,
            dimension_results=[make_unavailable()],
            answer_producer=bad_producer,
        )


async def test_manifest_only_allows_materialized_leaf_keys():
    _, _, answer = await run_pipeline(refs=(leaf_ref(),))
    manifest = answer.manifest
    key = sorted(manifest.allowed_evidence_ids())[0]
    allowed = manifest.allowed_evidence_ids()

    ok = ReaderAnswerEnvelope.model_validate(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [
                {"block_id": "b1", "text": "x", "evidence_refs": [key]}
            ],
        }
    )
    validate_answer_against_manifest(ok, allowed)

    forged = ReaderAnswerEnvelope.model_validate(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [
                {
                    "block_id": "b1",
                    "text": "x",
                    "evidence_refs": ["qp:1:0:10:" + "f" * 64],
                }
            ],
        }
    )
    with pytest.raises(ValueError):
        validate_answer_against_manifest(forged, allowed)


async def test_service_keeps_cutoff_and_lineage_in_answer():
    ref = leaf_ref()
    plan, source, answer = await run_pipeline(refs=(ref,))
    assert answer.plan is plan
    assert answer.manifest.owner_id == source.owner_id
    assert answer.manifest.version_id == source.version_id
    assert answer.manifest.snapshot_hash == source.snapshot_hash
