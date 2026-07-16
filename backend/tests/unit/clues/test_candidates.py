"""Deterministic cross-chapter candidate recall and evidence packages."""

from __future__ import annotations

import json

import pytest

from app.services.clues.candidates import (
    CandidateRecallConfig,
    ClueCandidateRecallService,
    HierarchyEvidenceNode,
    TimelineEventRef,
    stable_candidate_id,
)
from app.services.clues.evidence import (
    MAX_LATER_CHAPTERS,
    build_clue_evidence_package,
    clamp_later_units_to_scope,
    make_clue_evidence_unit,
    package_hash_for,
)
from app.services.clues.sources import (
    NullRelationshipObservationSource,
    RelationshipObservationRef,
    StaticRelationshipObservationSource,
    UnavailableRelationshipObservationSource,
)

pytestmark = pytest.mark.unit

HEX64_A = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64


def _node(
    node_id: str,
    chapter: int,
    start: int,
    text: str,
    *,
    entities: tuple[str, ...] = (),
    level: str = "evidence",
) -> HierarchyEvidenceNode:
    body = text
    end = start + max(len(body), 1)
    return HierarchyEvidenceNode(
        node_id=node_id,
        chapter_id=100 + chapter,
        narrative_chapter_number=chapter,
        source_start=start,
        source_end=end,
        content_hash=HEX64_A,
        content=body,
        level=level,
        entities=entities,
        order_index=start,
    )


def _base_nodes() -> list[HierarchyEvidenceNode]:
    return [
        _node("n1", 1, 0, "Alice found a silver key under the ash gate.", entities=("Alice",)),
        _node("n2", 1, 80, "Rain washed the courtyard stones.", entities=()),
        _node("n3", 3, 10, "Bob mentioned the silver key again near the ash gate.", entities=("Bob",)),
        _node("n4", 5, 0, "Alice unlocked the vault with the silver key.", entities=("Alice",)),
        _node("n5", 6, 0, "Unrelated market chatter about grain prices."),
    ]


@pytest.mark.asyncio
async def test_same_inputs_produce_stable_candidate_ids_hashes_and_order():
    service = ClueCandidateRecallService(
        relationship_source=NullRelationshipObservationSource()
    )
    nodes = _base_nodes()
    kwargs = dict(
        owner_id=1,
        novel_id=2,
        nodes=nodes,
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        vector_scores={"n3": 0.8, "n4": 0.9},
        config=CandidateRecallConfig(max_candidates=16, min_chapter_gap=1),
    )
    r1 = await service.build_candidates_from_nodes(**kwargs)
    r2 = await service.build_candidates_from_nodes(**kwargs)

    assert r1.drafts
    assert [d.candidate_id for d in r1.drafts] == [d.candidate_id for d in r2.drafts]
    assert [d.package_hash for d in r1.drafts] == [d.package_hash for d in r2.drafts]
    assert [d.candidate_id for d in r1.drafts] == sorted(
        d.candidate_id for d in r1.drafts
    )
    for d in r1.drafts:
        assert d.package.package_hash == package_hash_for(d.package.to_snapshot())
        assert d.package.cue_units
        assert d.package.later_units
        # Relationship outage recorded, not zero-signal substitute.
        assert r1.relationship_source is not None
        assert r1.relationship_source.status == "source_unavailable"
        rel = d.recall_signals.get("relationship") or {}
        assert rel.get("status") == "source_unavailable"


@pytest.mark.asyncio
async def test_recall_signals_do_not_become_state_and_include_reason_codes():
    service = ClueCandidateRecallService(
        relationship_source=StaticRelationshipObservationSource(
            [
                RelationshipObservationRef(
                    observation_ref="obs-1",
                    analysis_version_id=9,
                    source_character_id=1,
                    target_character_id=2,
                    relation_type="ally",
                    valid_from_chapter=3,
                    evidence_ids=("ev-n3",),
                )
            ]
        )
    )
    result = await service.build_candidates_from_nodes(
        owner_id=1,
        novel_id=2,
        nodes=_base_nodes(),
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        timeline_events=[
            TimelineEventRef(
                event_id=42,
                chapter_id=105,
                narrative_chapter_number=5,
                source_start=0,
                title="vault opened",
            )
        ],
        timeline_version_id=7,
        timeline_checksum=HEX64_C,
        vector_scores={"n4": 0.95},
        analysis_version_id=9,
    )
    assert result.drafts
    assert result.relationship_source is not None
    assert result.relationship_source.status == "ok"
    draft = result.drafts[0]
    # Signal bags present
    assert "adjacency" in draft.recall_signals or "lexical" in draft.recall_signals
    assert draft.reason_codes
    # No lifecycle state field on draft/package
    blob = json.dumps(draft.package.to_llm_payload())
    assert "lifecycle_status" not in blob
    assert "paid_off" not in blob or "allowed_classifications" in blob
    assert "active" not in draft.package.to_snapshot()


def test_stable_candidate_id_is_order_independent_for_later_ids():
    a = stable_candidate_id(
        cue_id="ev-1",
        later_ids=["ev-3", "ev-2"],
        reason_codes=["lexical_overlap", "adjacency"],
    )
    b = stable_candidate_id(
        cue_id="ev-1",
        later_ids=["ev-2", "ev-3"],
        reason_codes=["adjacency", "lexical_overlap"],
    )
    assert a == b
    assert a.startswith("clue-cand-")


def test_evidence_package_bounds_and_hash_stability():
    cue = make_clue_evidence_unit(
        evidence_id="ev-cue",
        chapter_id=1,
        narrative_chapter_number=1,
        text="a silver key under the ash gate",
        role_hint="cue",
    )
    later = make_clue_evidence_unit(
        evidence_id="ev-later",
        chapter_id=5,
        narrative_chapter_number=5,
        text="unlocked the vault with the silver key",
        role_hint="later",
    )
    p1 = build_clue_evidence_package(
        owner_id=1,
        novel_id=2,
        candidate_id="clue-cand-test",
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        cue_units=[cue],
        later_units=[later],
        recall_signals={"vector": {"n": 0.99}},
    )
    p2 = build_clue_evidence_package(
        owner_id=1,
        novel_id=2,
        candidate_id="clue-cand-test",
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        cue_units=[cue],
        later_units=[later],
        recall_signals={"vector": {"n": 0.99}},
    )
    assert p1.package_hash == p2.package_hash
    payload = p1.to_llm_payload()
    assert payload["allowed_evidence_ids"] == ["ev-cue", "ev-later"]
    assert payload["llm_contract"]["chat_is_not_evidence"] is True
    assert "full_novel" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_unavailable_relationship_source_is_not_zero_signal_success():
    service = ClueCandidateRecallService(
        relationship_source=UnavailableRelationshipObservationSource(detail="timeout")
    )
    result = await service.build_candidates_from_nodes(
        owner_id=1,
        novel_id=2,
        nodes=_base_nodes(),
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
    )
    assert result.relationship_source is not None
    assert result.relationship_source.status == "source_unavailable"
    assert result.relationship_source.items == []
    # Explicit unavailable differs from healthy empty.
    empty_service = ClueCandidateRecallService(
        relationship_source=StaticRelationshipObservationSource([])
    )
    empty = await empty_service.build_candidates_from_nodes(
        owner_id=1,
        novel_id=2,
        nodes=_base_nodes(),
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
    )
    assert empty.relationship_source is not None
    assert empty.relationship_source.status == "empty"
    assert empty.relationship_source.status != result.relationship_source.status


def test_candidates_module_has_no_lifecycle_write_calls():
    import app.services.clues.candidates as mod

    source = open(mod.__file__, encoding="utf-8").read()
    assert "ClueLifecycleEvent" not in source
    assert "session.add" not in source
    assert "AsyncSession" not in source or "load_hierarchy" in source
    assert "reader_chat" not in source
    assert "RelationshipObservation(" not in source


def test_clamp_later_units_prefers_chapters_closest_to_cue():
    """Later span > MAX_LATER_CHAPTERS is clamped; package build must succeed."""

    cue_chapter = 1
    later = [
        make_clue_evidence_unit(
            evidence_id=f"ev-ch{ch}",
            chapter_id=100 + ch,
            narrative_chapter_number=ch,
            text=f"payoff fragment in chapter {ch} about silver key",
            role_hint="later",
            source_start=0,
        )
        for ch in (2, 3, 4, 5, 6, 7, 8, 9)
    ]
    assert len({u.narrative_chapter_number for u in later}) > MAX_LATER_CHAPTERS

    scores = {u.evidence_id: 1.0 for u in later}
    # Far chapters would win on score alone if we did not prefer proximity.
    scores["ev-ch9"] = 9.0
    scores["ev-ch8"] = 8.0
    scores["ev-ch7"] = 7.0
    scores["ev-ch6"] = 6.0

    kept, omitted = clamp_later_units_to_scope(
        later,
        max_units=8,
        max_chapters=MAX_LATER_CHAPTERS,
        scores=scores,
        cue_chapter=cue_chapter,
    )
    kept_chapters = {u.narrative_chapter_number for u in kept}
    assert len(kept_chapters) <= MAX_LATER_CHAPTERS
    assert kept_chapters == {2, 3, 4, 5}
    assert "ev-ch9" in omitted
    assert kept

    cue = make_clue_evidence_unit(
        evidence_id="ev-cue",
        chapter_id=101,
        narrative_chapter_number=cue_chapter,
        text="Alice found a silver key under the ash gate",
        role_hint="cue",
    )
    package = build_clue_evidence_package(
        owner_id=1,
        novel_id=2,
        candidate_id="clue-cand-wide-later",
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        cue_units=[cue],
        later_units=kept,
        omitted_evidence_ids=omitted,
    )
    assert package.later_units
    assert len({u.narrative_chapter_number for u in package.later_units}) <= MAX_LATER_CHAPTERS


@pytest.mark.asyncio
async def test_wide_later_span_is_clamped_not_hard_fail():
    """Recall adjacency may cover >4 later chapters; run must keep building drafts."""

    # Cue in ch1; later nodes across 8 chapters (adjacency_chapter_window default=8).
    nodes = [
        _node("cue", 1, 0, "Alice found a silver key under the ash gate.", entities=("Alice",)),
    ]
    for ch in range(2, 10):
        nodes.append(
            _node(
                f"n{ch}",
                ch,
                0,
                f"Chapter {ch} mentions the silver key near the ash gate again.",
                entities=("Alice",) if ch % 2 == 0 else (),
            )
        )

    service = ClueCandidateRecallService(
        relationship_source=NullRelationshipObservationSource()
    )
    result = await service.build_candidates_from_nodes(
        owner_id=1,
        novel_id=91,
        nodes=nodes,
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-slime",
        hierarchy_checksum=HEX64_B,
        config=CandidateRecallConfig(
            max_candidates=32,
            min_chapter_gap=1,
            adjacency_chapter_window=8,
            max_later_chapters=MAX_LATER_CHAPTERS,
        ),
    )
    assert result.drafts, "wide later span must not zero-out all candidates"
    for draft in result.drafts:
        later_chapters = {u.narrative_chapter_number for u in draft.package.later_units}
        assert len(later_chapters) <= MAX_LATER_CHAPTERS
        assert draft.package.later_units
        # Package hash remains valid after clamp.
        assert draft.package.package_hash == package_hash_for(draft.package.to_snapshot())
        # Distant chapters dropped when span exceeds cap.
        cue_ch = draft.package.cue_units[0].narrative_chapter_number
        if cue_ch == 1:
            assert max(later_chapters) <= cue_ch + MAX_LATER_CHAPTERS
