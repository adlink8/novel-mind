"""Timeline Phase 07 evidence packages + deterministic scope gates."""

from __future__ import annotations

from hashlib import sha256

import pytest

from app.schemas.timeline import (
    EvidenceRef,
    EventCandidate,
    StoryTime,
    StoryTimeConstraint,
    TimelineExtraction,
)
from app.services.timeline.evidence import (
    EvidencePackage,
    EvidenceScopeError,
    EvidenceUnit,
    rebind_extraction_to_package,
    validate_extraction,
)

pytestmark = pytest.mark.unit

SS_HASH = "a" * 64
HIERARCHY_CHECKSUM = "b" * 64


def unit(
    evidence_id: str, start: int = 0, end: int = 5, text: str = "阿宁推开门"
) -> EvidenceUnit:
    return EvidenceUnit.create(evidence_id, start, end, text)


def package(*, units=None) -> EvidencePackage:
    return EvidencePackage.create(
        owner_id=1,
        novel_id=1,
        chapter_id=7,
        unit_id="chapter:7",
        source_snapshot_hash=SS_HASH,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HIERARCHY_CHECKSUM,
        units=units or [unit("ev-1"), unit("ev-2", 5, 10, "走向城头")],
    )


# ---------------------------------------------------------------------------
# EvidenceUnit
# ---------------------------------------------------------------------------


def test_evidence_unit_create_hashes_content():
    u = unit("ev-1")
    assert u.content_hash == sha256("阿宁推开门".encode("utf-8")).hexdigest()
    assert u.source_start == 0 and u.source_end == 5


def test_evidence_unit_create_rejects_bad_offsets():
    with pytest.raises(EvidenceScopeError, match="invalid evidence offsets"):
        unit("ev-x", start=-1, end=5)
    with pytest.raises(EvidenceScopeError, match="invalid evidence offsets"):
        unit("ev-x", start=5, end=5)


# ---------------------------------------------------------------------------
# EvidencePackage
# ---------------------------------------------------------------------------


def test_package_create_is_deterministic():
    p1 = package()
    p2 = package()
    assert p1.package_hash == p2.package_hash


def test_package_create_rejects_scope_and_hash_issues():
    with pytest.raises(EvidenceScopeError, match="scope and units"):
        EvidencePackage.create(
            owner_id=0,
            novel_id=1,
            chapter_id=7,
            unit_id="x",
            source_snapshot_hash=SS_HASH,
            hierarchy_build_id="b",
            hierarchy_checksum=HIERARCHY_CHECKSUM,
            units=[unit("ev-1")],
        )
    with pytest.raises(EvidenceScopeError, match="scope and units"):
        EvidencePackage.create(
            owner_id=1,
            novel_id=1,
            chapter_id=7,
            unit_id="x",
            source_snapshot_hash=SS_HASH,
            hierarchy_build_id="b",
            hierarchy_checksum=HIERARCHY_CHECKSUM,
            units=[],
        )
    with pytest.raises(EvidenceScopeError, match="SHA-256"):
        EvidencePackage.create(
            owner_id=1,
            novel_id=1,
            chapter_id=7,
            unit_id="x",
            source_snapshot_hash="short",
            hierarchy_build_id="b",
            hierarchy_checksum=HIERARCHY_CHECKSUM,
            units=[unit("ev-1")],
        )


def test_package_create_rejects_duplicate_evidence_ids():
    with pytest.raises(EvidenceScopeError, match="unique within a package"):
        package(units=[unit("ev-dup"), unit("ev-dup", 5, 10, "其他文本")])


# ---------------------------------------------------------------------------
# rebind_extraction_to_package
# ---------------------------------------------------------------------------


def _event(
    candidate_id: str,
    *,
    chapter_number: int = 7,
    evidence_ids=(),
    confidence: float = 0.9,
) -> EventCandidate:
    return EventCandidate(
        candidate_id=candidate_id,
        title="事件",
        description="描述",
        event_type="plot",
        narrative_chapter_number=chapter_number,
        narrative_index=0,
        story_time=StoryTime(precision="unknown"),
        evidence=[
            EvidenceRef(
                chapter_id=7,  # bogus offsets; package authority must overwrite
                evidence_id=eid,
                source_start=0,
                source_end=1,
                content_hash="0" * 64,
            )
            for eid in evidence_ids
        ],
        confidence=confidence,
    )


def test_rebind_overwrites_offsets_hash_and_drops_unknown():
    pkg = package()
    extraction = TimelineExtraction(
        events=[
            _event("e1", evidence_ids=("ev-1", "ev-ghost", "ev-1")),
            _event("e2", evidence_ids=("ev-ghost",)),
        ],
        story_time_constraints=[],
    )
    rebound = rebind_extraction_to_package(pkg, extraction)
    # e2 has no valid evidence -> dropped; e1 keeps one unique rebind
    assert [e.candidate_id for e in rebound.events] == ["e1"]
    ref = rebound.events[0].evidence[0]
    assert ref.chapter_id == 7
    assert ref.source_start == 0 and ref.source_end == 5
    assert ref.content_hash == pkg.units[0].content_hash
    assert rebound.events[0].narrative_chapter_number == 7


def test_rebind_filters_constraints_to_surviving_events():
    pkg = package()
    extraction = TimelineExtraction(
        events=[
            _event("e1", evidence_ids=("ev-1",)),
            _event("e2", evidence_ids=("ev-2",)),
        ],
        story_time_constraints=[
            StoryTimeConstraint(
                source_candidate_id="e1",
                target_candidate_id="e2",
                relation="before",
                evidence_ids=["ev-1", "ev-ghost"],
            ),
            StoryTimeConstraint(
                source_candidate_id="e1",
                target_candidate_id="dropped",
                relation="before",
                evidence_ids=["ev-1"],
            ),
        ],
    )
    rebound = rebind_extraction_to_package(pkg, extraction)
    assert [c.source_candidate_id for c in rebound.story_time_constraints] == ["e1"]
    assert rebound.story_time_constraints[0].evidence_ids == ["ev-1"]


# ---------------------------------------------------------------------------
# validate_extraction
# ---------------------------------------------------------------------------


def test_validate_extraction_accepts_rebound_output():
    pkg = package()
    rebound = rebind_extraction_to_package(
        pkg,
        TimelineExtraction(
            events=[_event("e1", evidence_ids=("ev-1",))],
            story_time_constraints=[],
        ),
    )
    validate_extraction(pkg, rebound)  # must not raise


def test_validate_extraction_rejects_duplicate_candidate_ids():
    pkg = package()
    units_by_id = {u.evidence_id: u for u in pkg.units}

    def valid_event(candidate_id, eid):
        u = units_by_id[eid]
        return EventCandidate(
            candidate_id=candidate_id,
            title="事件",
            description="描述",
            event_type="plot",
            narrative_chapter_number=7,
            narrative_index=0,
            story_time=StoryTime(precision="unknown"),
            evidence=[
                EvidenceRef(
                    chapter_id=7,
                    evidence_id=u.evidence_id,
                    source_start=u.source_start,
                    source_end=u.source_end,
                    content_hash=u.content_hash,
                )
            ],
            confidence=0.9,
        )

    extraction = TimelineExtraction(
        events=[valid_event("e1", "ev-1"), valid_event("e1", "ev-2")],
        story_time_constraints=[],
    )
    with pytest.raises(EvidenceScopeError, match="unique within a chapter"):
        validate_extraction(pkg, extraction)


def test_validate_extraction_rejects_wrong_chapter_number():
    pkg = package()
    extraction = TimelineExtraction(
        events=[_event("e1", chapter_number=3, evidence_ids=("ev-1",))],
        story_time_constraints=[],
    )
    with pytest.raises(EvidenceScopeError, match="outside the package"):
        validate_extraction(pkg, extraction)


def test_validate_extraction_rejects_unknown_evidence():
    pkg = package()
    extraction = TimelineExtraction(
        events=[_event("e1", evidence_ids=("ev-ghost",))],
        story_time_constraints=[],
    )
    with pytest.raises(EvidenceScopeError, match="cross-chapter or unknown"):
        validate_extraction(pkg, extraction)


def test_validate_extraction_rejects_offset_hash_mismatch():
    pkg = package()
    event = _event("e1", evidence_ids=("ev-1",))
    event = event.model_copy(
        update={
            "evidence": [
                EvidenceRef(
                    chapter_id=7,
                    evidence_id="ev-1",
                    source_start=0,
                    source_end=5,
                    content_hash="0" * 64,  # wrong
                )
            ]
        }
    )
    with pytest.raises(EvidenceScopeError, match="offset or content hash mismatch"):
        validate_extraction(
            pkg, TimelineExtraction(events=[event], story_time_constraints=[])
        )


def test_validate_extraction_rejects_bad_constraint_refs():
    pkg = package()
    units_by_id = {u.evidence_id: u for u in pkg.units}
    ev = _event("e1", evidence_ids=("ev-1",))
    u = units_by_id["ev-1"]
    ev = ev.model_copy(
        update={
            "evidence": [
                EvidenceRef(
                    chapter_id=7,
                    evidence_id=u.evidence_id,
                    source_start=u.source_start,
                    source_end=u.source_end,
                    content_hash=u.content_hash,
                )
            ]
        }
    )
    extraction = TimelineExtraction(
        events=[ev],
        story_time_constraints=[
            StoryTimeConstraint(
                source_candidate_id="e1",
                target_candidate_id="nope",
                relation="before",
                evidence_ids=["ev-1"],
            )
        ],
    )
    with pytest.raises(EvidenceScopeError, match="unknown candidate"):
        validate_extraction(pkg, extraction)
