"""Unit tests for exact selection validation and visible-context manifests (10-03)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.schemas.reader_chat import SelectionCoordinate
from app.services.reader_chat.context import (
    SELECTION_EVIDENCE_KEY,
    ContextEvidenceEntry,
    ContextManifest,
    SelectionValidationError,
    assemble_context_manifest,
    canonical_manifest_checksum,
    code_point_len,
    code_point_slice,
    content_sha256,
    freeze_manifest_from_stored,
    validate_selection,
)
from app.services.reader_chat.retrieval import (
    RelationshipObservationEvidence,
    RelationshipObservationItem,
    revalidate_observation_item,
)

pytestmark = pytest.mark.unit

HEX64_A = "a" * 64
HEX64_B = "b" * 64


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Unicode / code-point slicing (D-03)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end,expected",
    [
        ("你好世界", 0, 2, "你好"),
        ("a😀b", 1, 2, "😀"),
        ("e\u0301x", 0, 2, "e\u0301"),  # combining acute
        ("line1\r\nline2", 5, 7, "\r\n"),
        ("重复重复尾", 0, 2, "重复"),
        ("重复重复尾", 2, 4, "重复"),
    ],
)
def test_code_point_slice_handles_unicode_adversarial_cases(text, start, end, expected):
    assert code_point_slice(text, start, end) == expected
    assert code_point_len(expected) == end - start


def test_code_point_slice_rejects_out_of_bounds():
    with pytest.raises(SelectionValidationError) as exc:
        code_point_slice("短", 0, 5)
    assert exc.value.code == "invalid_bounds"


def test_content_sha256_is_utf8_bytes_digest():
    text = "中文+emoji😀"
    assert content_sha256(text) == _sha(text)


# ---------------------------------------------------------------------------
# Selection validation against Chapter.content authority
# ---------------------------------------------------------------------------


def _selection(content: str, start: int, end: int, **overrides: Any) -> SelectionCoordinate:
    exact = content[start:end]
    payload = {
        "chapter_id": 1,
        "source_start": start,
        "source_end": end,
        "selection_text": exact,
        "selection_text_hash": _sha(exact),
        "chapter_content_hash": _sha(content),
    }
    payload.update(overrides)
    return SelectionCoordinate(**payload)


def _mock_session_with_chapter(*, chapter_id: int, novel_id: int, content: str, chapter_number: int = 1):
    chapter = SimpleNamespace(
        id=chapter_id,
        novel_id=novel_id,
        chapter_number=chapter_number,
        content=content,
    )
    novel = SimpleNamespace(id=novel_id, owner_id=1, reading_progress={})
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=chapter)
    return session, novel, chapter


@pytest.mark.asyncio
async def test_validate_selection_accepts_exact_cjk_slice():
    content = "开篇：龙与精灵在山谷结盟。后续章节隐藏。"
    session, novel, chapter = _mock_session_with_chapter(
        chapter_id=11, novel_id=22, content=content
    )
    # resolve_active_hierarchy path: scalar returns chapter then None for pointer
    session.scalar = AsyncMock(side_effect=[chapter, None])
    coord = SelectionCoordinate(
        chapter_id=chapter.id,
        source_start=3,
        source_end=12,
        selection_text=content[3:12],
        selection_text_hash=_sha(content[3:12]),
        chapter_content_hash=_sha(content),
    )
    validated = await validate_selection(
        session, novel=novel, owner_id=1, selection=coord
    )
    assert validated.selection_text == content[3:12]
    assert validated.selection_text_hash == _sha(content[3:12])


@pytest.mark.asyncio
async def test_validate_selection_rejects_stale_chapter_hash():
    content = "原始正文ABC"
    session, novel, chapter = _mock_session_with_chapter(
        chapter_id=1, novel_id=2, content=content
    )
    coord = SelectionCoordinate(
        chapter_id=chapter.id,
        source_start=0,
        source_end=2,
        selection_text=content[0:2],
        selection_text_hash=_sha(content[0:2]),
        chapter_content_hash=HEX64_B,
    )
    with pytest.raises(SelectionValidationError) as exc:
        await validate_selection(session, novel=novel, owner_id=1, selection=coord)
    assert exc.value.code == "stale_chapter"


@pytest.mark.asyncio
async def test_validate_selection_rejects_mismatched_text_and_empty():
    content = "abcdef"
    session, novel, chapter = _mock_session_with_chapter(
        chapter_id=1, novel_id=2, content=content
    )
    forged = SelectionCoordinate(
        chapter_id=chapter.id,
        source_start=0,
        source_end=3,
        selection_text="zzz",
        selection_text_hash=_sha("zzz"),
        chapter_content_hash=_sha(content),
    )
    with pytest.raises(SelectionValidationError) as exc:
        await validate_selection(session, novel=novel, owner_id=1, selection=forged)
    assert exc.value.code == "stale_selection"


@pytest.mark.asyncio
async def test_validate_selection_rejects_invalid_bounds():
    content = "短文"
    session, novel, chapter = _mock_session_with_chapter(
        chapter_id=1, novel_id=2, content=content
    )
    coord = SelectionCoordinate(
        chapter_id=chapter.id,
        source_start=0,
        source_end=99,
        selection_text=content,
        selection_text_hash=_sha(content),
        chapter_content_hash=_sha(content),
    )
    with pytest.raises(SelectionValidationError) as exc:
        await validate_selection(session, novel=novel, owner_id=1, selection=coord)
    assert exc.value.code == "invalid_bounds"


# ---------------------------------------------------------------------------
# Relationship observation revalidation (D-10)
# ---------------------------------------------------------------------------


def _obs(**kwargs: Any) -> RelationshipObservationItem:
    base = dict(
        observation_id=1,
        analysis_version_id=10,
        owner_id=1,
        novel_id=2,
        source_character_id=3,
        target_character_id=4,
        relation_type="ally",
        valid_from_chapter=1,
        valid_to_chapter=None,
        status="accepted",
        evidence=(
            RelationshipObservationEvidence(
                evidence_id="ev1",
                chapter_id=100,
                source_start=0,
                source_end=4,
                content_hash=HEX64_A,
                chapter_number=1,
            ),
        ),
    )
    base.update(kwargs)
    return RelationshipObservationItem(**base)


def test_revalidate_observation_drops_future_and_unaccepted():
    visible = revalidate_observation_item(
        _obs(valid_from_chapter=1),
        owner_id=1,
        novel_id=2,
        version_id=10,
        cutoff_chapter=1,
        full_book=False,
    )
    assert visible is not None

    future = revalidate_observation_item(
        _obs(valid_from_chapter=9),
        owner_id=1,
        novel_id=2,
        version_id=10,
        cutoff_chapter=1,
        full_book=False,
    )
    assert future is None

    unaccepted = revalidate_observation_item(
        _obs(status="candidate"),
        owner_id=1,
        novel_id=2,
        version_id=10,
        cutoff_chapter=1,
        full_book=False,
    )
    assert unaccepted is None

    wrong_owner = revalidate_observation_item(
        _obs(owner_id=99),
        owner_id=1,
        novel_id=2,
        version_id=10,
        cutoff_chapter=1,
        full_book=False,
    )
    assert wrong_owner is None


def test_revalidate_observation_full_book_keeps_late_chapters():
    late = revalidate_observation_item(
        _obs(valid_from_chapter=99),
        owner_id=1,
        novel_id=2,
        version_id=10,
        cutoff_chapter=1,
        full_book=True,
    )
    assert late is not None


# ---------------------------------------------------------------------------
# Manifest checksum / retry / dialogue non-evidence (D-03, D-05)
# ---------------------------------------------------------------------------


def test_canonical_manifest_checksum_is_order_stable():
    e1 = ContextEvidenceEntry(
        evidence_key=SELECTION_EVIDENCE_KEY,
        source_type="selection",
        source_id="1:0:2",
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=2,
        content_hash=HEX64_A,
        excerpt="你好",
        sort_order=0,
    )
    e2 = ContextEvidenceEntry(
        evidence_key="hierarchy:n1",
        source_type="hierarchy",
        source_id="n1",
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=4,
        content_hash=HEX64_B,
        excerpt="可见证据",
        sort_order=1,
    )
    payload = {
        "reading_progress_snapshot": {"chapter_id": 1, "full_book": False},
        "full_book": False,
        "cutoff_chapter_number": 1,
        "analysis_version_id": 7,
        "hierarchy_build_id": "build-1",
        "hierarchy_checksum": HEX64_A,
        "evidence": [e1.canonical_dict(), e2.canonical_dict()],
        "omitted_evidence_counts": {"hierarchy": 0},
        "prompt_inputs": {
            "dialogue_framing": {
                "label": "CONVERSATIONAL_FRAMING_NOT_EVIDENCE",
                "is_evidence": False,
            }
        },
        "source_status": {"hierarchy": "ok"},
    }
    c1 = canonical_manifest_checksum(payload)
    c2 = canonical_manifest_checksum(payload)
    assert c1 == c2
    assert len(c1) == 64

    # Order of evidence keys in allowed list must affect checksum if changed.
    payload2 = dict(payload)
    payload2["evidence"] = [e2.canonical_dict(), e1.canonical_dict()]
    assert canonical_manifest_checksum(payload2) != c1


def test_freeze_manifest_from_stored_rejects_tampered_checksum():
    entry = ContextEvidenceEntry(
        evidence_key=SELECTION_EVIDENCE_KEY,
        source_type="selection",
        source_id="1:0:1",
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=1,
        content_hash=HEX64_A,
        excerpt="x",
        sort_order=0,
    )
    draft = ContextManifest(
        reading_progress_snapshot={"chapter_id": 1},
        full_book=False,
        cutoff_chapter_number=1,
        analysis_version_id=None,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX64_A,
        evidence=(entry,),
        omitted_evidence_counts={},
        prompt_inputs={"dialogue_framing": {"is_evidence": False}},
        source_status={"selection": "ok"},
        manifest_checksum="",
    )
    good = canonical_manifest_checksum(draft.canonical_payload())
    frozen = freeze_manifest_from_stored(
        reading_progress_snapshot=draft.reading_progress_snapshot,
        full_book=False,
        cutoff_chapter_number=1,
        analysis_version_id=None,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX64_A,
        evidence=[entry],
        omitted_evidence_counts={},
        prompt_inputs=draft.prompt_inputs,
        source_status=draft.source_status,
        expected_checksum=good,
    )
    assert frozen.manifest_checksum == good
    # Retry rehydrate is independent of current progress: same checksum.
    again = freeze_manifest_from_stored(
        reading_progress_snapshot=draft.reading_progress_snapshot,
        full_book=False,
        cutoff_chapter_number=1,
        analysis_version_id=None,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX64_A,
        evidence=[entry.canonical_dict()],
        omitted_evidence_counts={},
        prompt_inputs=draft.prompt_inputs,
        source_status=draft.source_status,
        expected_checksum=good,
    )
    assert again.manifest_checksum == good

    with pytest.raises(SelectionValidationError) as exc:
        freeze_manifest_from_stored(
            reading_progress_snapshot=draft.reading_progress_snapshot,
            full_book=False,
            cutoff_chapter_number=1,
            analysis_version_id=None,
            hierarchy_build_id="b",
            hierarchy_checksum=HEX64_A,
            evidence=[entry],
            omitted_evidence_counts={},
            prompt_inputs=draft.prompt_inputs,
            source_status=draft.source_status,
            expected_checksum=HEX64_B,
        )
    assert exc.value.code == "manifest_checksum_mismatch"


def test_dialogue_framing_is_flagged_non_evidence_in_prompt_inputs():
    # Pure structural expectation used by assemble_context_manifest.
    from app.services.reader_chat.context import _dialogue_framing

    framing = _dialogue_framing(
        [{"role": "user", "body": "之前的问题", "sequence": 1}]
    )
    assert framing["label"] == "CONVERSATIONAL_FRAMING_NOT_EVIDENCE"
    assert framing["is_evidence"] is False
    assert "body" not in framing["turns"][0]
    assert framing["turns"][0]["body_hash"] == _sha("之前的问题")


@pytest.mark.asyncio
async def test_assemble_rejects_client_forged_evidence_refs():
    from app.services.reader_chat.context import ValidatedSelection

    content = "可见选区正文"
    novel = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    selection = ValidatedSelection(
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=2,
        selection_text=content[0:2],
        selection_text_hash=_sha(content[0:2]),
        chapter_content_hash=_sha(content),
        hierarchy_build_id="none",
        hierarchy_checksum=HEX64_A,
    )
    session = AsyncMock()
    with pytest.raises(SelectionValidationError) as exc:
        await assemble_context_manifest(
            session,
            novel=novel,
            owner_id=1,
            selection=selection,
            client_evidence_keys=["hierarchy:forged-secret"],
        )
    assert exc.value.code == "forged_evidence_refs"


def test_resolve_chapter_cutoff_is_public_export():
    from app.services.timeline import query as tq

    assert callable(tq.resolve_chapter_cutoff)
    assert callable(tq._chapter_cutoff)


@pytest.mark.asyncio
async def test_phase09_reader_binding_requires_load_filtered_contract():
    from app.services.reader_chat.retrieval import Phase09RelationshipObservationReader

    with pytest.raises(RuntimeError, match="load_filtered_relationship_graph"):
        Phase09RelationshipObservationReader(service=SimpleNamespace())

    # Real Phase 09 service exposes the contract — binding must succeed.
    from app.services.relationships.query import relationship_graph_query_service

    reader = Phase09RelationshipObservationReader(service=relationship_graph_query_service)
    assert reader is not None
