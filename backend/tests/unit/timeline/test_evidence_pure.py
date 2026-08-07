"""Timeline evidence package pure functions (task 13).

Covers EvidenceUnit.create / EvidencePackage.create validation gates and the
pure rebind_extraction_to_package / validate_extraction behaviors that the
adversarial suite exercises in aggregate but not individually.
"""

from __future__ import annotations

import pytest

from app.schemas.timeline import TimelineExtraction
from app.services.timeline.evidence import (
    EvidencePackage,
    EvidenceScopeError,
    EvidenceUnit,
    rebind_extraction_to_package,
    validate_extraction,
)

pytestmark = pytest.mark.unit

TEXT = "At dawn, Mira opened the western gate."


def package(units=None) -> EvidencePackage:
    return EvidencePackage.create(
        owner_id=3,
        novel_id=8,
        chapter_id=5,
        unit_id="scene-5-1",
        source_snapshot_hash="1" * 64,
        hierarchy_build_id="build-8",
        hierarchy_checksum="2" * 64,
        units=units or [EvidenceUnit.create("ev-1", 0, len(TEXT), TEXT)],
    )


# ── EvidenceUnit.create ──


def test_evidence_unit_create_hashes_text():
    unit = EvidenceUnit.create("ev-1", 0, len(TEXT), TEXT)
    assert unit.evidence_id == "ev-1"
    assert unit.content_hash == EvidenceUnit.create("ev-1", 0, len(TEXT), TEXT).content_hash


def test_evidence_unit_create_rejects_negative_start():
    with pytest.raises(EvidenceScopeError):
        EvidenceUnit.create("ev-1", -1, 5, TEXT)


def test_evidence_unit_create_rejects_zero_length():
    with pytest.raises(EvidenceScopeError):
        EvidenceUnit.create("ev-1", 0, 0, TEXT)


def test_evidence_unit_create_rejects_reversed_offsets():
    with pytest.raises(EvidenceScopeError):
        EvidenceUnit.create("ev-1", 5, 2, TEXT)


# ── EvidencePackage.create ──


def test_package_create_requires_positive_scope():
    with pytest.raises(EvidenceScopeError):
        EvidencePackage.create(
            owner_id=0,
            novel_id=8,
            chapter_id=5,
            unit_id="u",
            source_snapshot_hash="1" * 64,
            hierarchy_build_id="b",
            hierarchy_checksum="2" * 64,
            units=[EvidenceUnit.create("ev-1", 0, 1, "x")],
        )


def test_package_create_requires_units():
    with pytest.raises(EvidenceScopeError):
        EvidencePackage.create(
            owner_id=3,
            novel_id=8,
            chapter_id=5,
            unit_id="u",
            source_snapshot_hash="1" * 64,
            hierarchy_build_id="b",
            hierarchy_checksum="2" * 64,
            units=[],
        )


def test_package_create_requires_sha256_lineage():
    with pytest.raises(EvidenceScopeError):
        EvidencePackage.create(
            owner_id=3,
            novel_id=8,
            chapter_id=5,
            unit_id="u",
            source_snapshot_hash="short",
            hierarchy_build_id="b",
            hierarchy_checksum="2" * 64,
            units=[EvidenceUnit.create("ev-1", 0, 1, "x")],
        )


def test_package_create_rejects_duplicate_evidence_ids():
    with pytest.raises(EvidenceScopeError):
        EvidencePackage.create(
            owner_id=3,
            novel_id=8,
            chapter_id=5,
            unit_id="u",
            source_snapshot_hash="1" * 64,
            hierarchy_build_id="b",
            hierarchy_checksum="2" * 64,
            units=[
                EvidenceUnit.create("ev-1", 0, 1, "x"),
                EvidenceUnit.create("ev-1", 1, 2, "y"),
            ],
        )


def test_package_create_deterministic_hash():
    assert package().package_hash == package().package_hash


def test_package_create_hash_changes_with_units():
    other = EvidencePackage.create(
        owner_id=3,
        novel_id=8,
        chapter_id=5,
        unit_id="scene-5-1",
        source_snapshot_hash="1" * 64,
        hierarchy_build_id="build-8",
        hierarchy_checksum="2" * 64,
        units=[EvidenceUnit.create("ev-2", 0, len(TEXT), TEXT)],
    )
    assert package().package_hash != other.package_hash


# ── rebind_extraction_to_package ──


import hashlib

HASH64 = "0" * 64
VALID_START = 0
VALID_END = len(TEXT)
VALID_HASH = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()


def _extraction(
    evidence_ids=(("ev-1", VALID_START, VALID_END, VALID_HASH),),
    constraints=(),
    second_event=False,
) -> TimelineExtraction:
    events = [
        {
            "candidate_id": "c1",
            "title": "开门",
            "description": "Mira 打开城门",
            "event_type": "plot",
            "narrative_chapter_number": 5,
            "narrative_index": 0,
            "participants": [],
            "story_time": {"precision": "unknown", "expression": None},
            "evidence": [
                {
                    "chapter_id": 5,
                    "evidence_id": eid,
                    "source_start": s,
                    "source_end": e,
                    "content_hash": h,
                }
                for eid, s, e, h in evidence_ids
            ],
            "confidence": 0.8,
        }
    ]
    if second_event:
        events.append(
            {
                "candidate_id": "c2",
                "title": "闭门",
                "description": "Mira 关闭城门",
                "event_type": "plot",
                "narrative_chapter_number": 5,
                "narrative_index": 1,
                "participants": [],
                "story_time": {"precision": "unknown", "expression": None},
            "evidence": [
                {
                    "chapter_id": 5,
                    "evidence_id": "ev-1",
                    "source_start": VALID_START,
                    "source_end": VALID_END,
                    "content_hash": VALID_HASH,
                }
            ],
            "confidence": 0.7,
        }
    )
    return TimelineExtraction.model_validate(
        {
            "events": events,
            "story_time_constraints": list(constraints),
        }
    )


def test_rebind_overwrites_offsets_and_hash_from_package():
    p = package()
    # LLM 给出了错误的 offsets/hash/chapter；都应被包权威覆写。
    rebound = rebind_extraction_to_package(
        p, _extraction([("ev-1", 999, 1000, HASH64)])
    )
    ref = rebound.events[0].evidence[0]
    unit = p.units[0]
    assert ref.chapter_id == p.chapter_id
    assert ref.source_start == unit.source_start
    assert ref.source_end == unit.source_end
    assert ref.content_hash == unit.content_hash


def test_rebind_drops_unknown_evidence_and_empty_events():
    p = package()
    rebound = rebind_extraction_to_package(p, _extraction([("ghost", 0, 1, HASH64)]))
    assert rebound.events == []


def test_rebind_deduplicates_repeated_evidence_ids():
    p = package()
    rebound = rebind_extraction_to_package(
        p, _extraction([("ev-1", 0, 1, HASH64), ("ev-1", 2, 3, HASH64)])
    )
    assert len(rebound.events[0].evidence) == 1


def test_rebind_filters_constraints_with_missing_candidates_or_evidence():
    from app.schemas.timeline import StoryTimeConstraint

    p = package()
    extraction = _extraction(
        second_event=True,
        constraints=[
            StoryTimeConstraint(
                source_candidate_id="c1",
                target_candidate_id="ghost",
                relation="before",
                evidence_ids=["ev-1"],
            ),
            StoryTimeConstraint(
                source_candidate_id="c1",
                target_candidate_id="c2",
                relation="after",
                evidence_ids=["ghost-ev"],
            ),
            StoryTimeConstraint(
                source_candidate_id="c1",
                target_candidate_id="c2",
                relation="simultaneous",
                evidence_ids=["ev-1"],
            ),
        ],
    )
    rebound = rebind_extraction_to_package(p, extraction)
    assert [c.relation for c in rebound.story_time_constraints] == ["simultaneous"]


# ── validate_extraction ──


def _valid_extraction():
    p = package()
    unit = p.units[0]
    return p, TimelineExtraction.model_validate(
        {
            "events": [
                {
                    "candidate_id": "c1",
                    "title": "开门",
                    "description": "Mira 打开城门",
                    "event_type": "plot",
                    "narrative_chapter_number": p.chapter_id,
                    "narrative_index": 0,
                    "participants": [],
                    "story_time": {"precision": "unknown", "expression": None},
                    "evidence": [
                        {
                            "chapter_id": p.chapter_id,
                            "evidence_id": "ev-1",
                            "source_start": unit.source_start,
                            "source_end": unit.source_end,
                            "content_hash": unit.content_hash,
                        }
                    ],
                    "confidence": 0.8,
                }
            ],
            "story_time_constraints": [],
        }
    )


def test_validate_extraction_accepts_consistent_package():
    p, extraction = _valid_extraction()
    validate_extraction(p, extraction)  # 不应抛异常


def test_validate_extraction_rejects_duplicate_candidate_ids():
    p = package()
    unit = p.units[0]
    base = {
        "title": "开门",
        "description": "Mira 打开城门",
        "event_type": "plot",
        "narrative_chapter_number": p.chapter_id,
        "narrative_index": 0,
        "participants": [],
        "story_time": {"precision": "unknown", "expression": None},
        "evidence": [
            {
                "chapter_id": p.chapter_id,
                "evidence_id": "ev-1",
                "source_start": unit.source_start,
                "source_end": unit.source_end,
                "content_hash": unit.content_hash,
            }
        ],
        "confidence": 0.8,
    }
    extraction = TimelineExtraction.model_validate(
        {
            "events": [
                {**base, "candidate_id": "dup", "narrative_index": 0},
                {**base, "candidate_id": "dup", "narrative_index": 1},
            ],
            "story_time_constraints": [],
        }
    )
    with pytest.raises(EvidenceScopeError, match="unique"):
        validate_extraction(p, extraction)


def test_validate_extraction_rejects_wrong_chapter():
    p, extraction = _valid_extraction()
    extraction.events[0].narrative_chapter_number = 9
    with pytest.raises(EvidenceScopeError, match="outside the package"):
        validate_extraction(p, extraction)


def test_validate_extraction_rejects_unknown_evidence():
    p, extraction = _valid_extraction()
    extraction.events[0].evidence[0].evidence_id = "ghost"
    with pytest.raises(EvidenceScopeError, match="unknown evidence"):
        validate_extraction(p, extraction)


def test_validate_extraction_rejects_offset_or_hash_mismatch():
    p, extraction = _valid_extraction()
    extraction.events[0].evidence[0].source_start = 123
    with pytest.raises(EvidenceScopeError, match="offset or content hash"):
        validate_extraction(p, extraction)


def test_validate_extraction_rejects_constraint_unknown_candidate():
    from app.schemas.timeline import StoryTimeConstraint

    p = package()
    extraction = _extraction(
        second_event=True,
        constraints=[
            StoryTimeConstraint(
                source_candidate_id="c1",
                target_candidate_id="ghost",
                relation="before",
                evidence_ids=["ev-1"],
            )
        ],
    )
    with pytest.raises(EvidenceScopeError, match="unknown candidate"):
        validate_extraction(p, extraction)


def test_validate_extraction_rejects_constraint_unknown_evidence():
    from app.schemas.timeline import StoryTimeConstraint

    p = package()
    extraction = _extraction(
        second_event=True,
        constraints=[
            StoryTimeConstraint(
                source_candidate_id="c1",
                target_candidate_id="c2",
                relation="before",
                evidence_ids=["ghost-ev"],
            )
        ],
    )
    with pytest.raises(EvidenceScopeError, match="unknown evidence"):
        validate_extraction(p, extraction)
