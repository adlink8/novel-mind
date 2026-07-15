"""Phase 09 null/outage protocols and chat non-authority for clue sources."""

from __future__ import annotations

import pytest

from app.services.clues.sources import (
    NullRelationshipObservationSource,
    Phase09BoundRelationshipSource,
    PrimarySelectionCitationRef,
    RelationshipObservationRef,
    StaticRelationshipObservationSource,
    UnavailableRelationshipObservationSource,
    accept_primary_selection_citation_refs,
    reject_freeform_chat_as_evidence,
)

pytestmark = [pytest.mark.integration, pytest.mark.unit]

HEX64 = "a" * 64


@pytest.mark.asyncio
async def test_null_source_records_source_unavailable_not_empty():
    src = NullRelationshipObservationSource()
    result = await src.list_observations(owner_id=1, novel_id=2)
    assert result.status == "source_unavailable"
    assert result.items == []
    assert result.reason_code == "source_unavailable"
    signals = result.recall_signals()
    assert signals["relationship"]["status"] == "source_unavailable"
    assert signals["relationship"]["count"] == 0
    # Must not look like a successful empty read.
    assert signals["relationship"]["status"] != "empty"


@pytest.mark.asyncio
async def test_bound_reader_outage_is_source_unavailable():
    async def boom(**_kwargs):
        raise RuntimeError("pg down")

    src = Phase09BoundRelationshipSource(reader=boom)
    result = await src.list_observations(owner_id=1, novel_id=2, analysis_version_id=3)
    assert result.status == "source_unavailable"
    assert "phase09_reader_error" in (result.detail or "")
    assert result.items == []


@pytest.mark.asyncio
async def test_bound_reader_none_is_unavailable_not_zero_success():
    src = Phase09BoundRelationshipSource(reader=None)
    result = await src.list_observations(owner_id=1, novel_id=2)
    assert result.status == "source_unavailable"


@pytest.mark.asyncio
async def test_healthy_empty_reader_is_empty_not_unavailable():
    async def empty(**_kwargs):
        return []

    src = Phase09BoundRelationshipSource(reader=empty)
    result = await src.list_observations(owner_id=1, novel_id=2)
    assert result.status == "empty"
    assert result.status != "source_unavailable"


@pytest.mark.asyncio
async def test_static_source_filters_by_version_and_chapter():
    src = StaticRelationshipObservationSource(
        [
            RelationshipObservationRef(
                observation_ref="obs-a",
                analysis_version_id=1,
                source_character_id=1,
                target_character_id=2,
                relation_type="ally",
                valid_from_chapter=2,
            ),
            RelationshipObservationRef(
                observation_ref="obs-b",
                analysis_version_id=2,
                source_character_id=1,
                target_character_id=3,
                relation_type="enemy",
                valid_from_chapter=5,
            ),
        ]
    )
    r = await src.list_observations(
        owner_id=1, novel_id=2, analysis_version_id=1, through_chapter=3
    )
    assert r.status == "ok"
    assert len(r.items) == 1
    assert r.items[0].observation_ref == "obs-a"


def test_selection_citation_accepted_freeform_chat_rejected():
    ok = accept_primary_selection_citation_refs(
        [
            {
                "ref_id": "sel-1",
                "chapter_id": 1,
                "source_start": 0,
                "source_end": 12,
                "content_hash": HEX64,
                "kind": "selection",
                "excerpt": "silver key",
            }
        ]
    )
    assert ok.status == "ok"
    assert isinstance(ok.items[0], PrimarySelectionCitationRef)

    freeform = accept_primary_selection_citation_refs(
        [
            {
                "ref_id": "msg-1",
                "chapter_id": 1,
                "source_start": 0,
                "source_end": 12,
                "content_hash": HEX64,
                "kind": "selection",
                "message_text": "I think this is a clue about the key",
            }
        ]
    )
    assert freeform.status == "rejected"
    assert freeform.reason_code == "freeform_or_malformed_rejected"

    chat = reject_freeform_chat_as_evidence("The silver key is definitely a foreshadow.")
    assert chat.status == "rejected"
    assert chat.reason_code == "chat_freeform_forbidden"


def test_sources_module_does_not_import_chat_or_relationship_business():
    import app.services.clues.sources as mod

    source = open(mod.__file__, encoding="utf-8").read()
    assert "app.services.reader_chat" not in source
    assert "app.services.relationships" not in source
    assert "RelationshipObservationWorker" not in source
    assert "reader_conversations" not in source


@pytest.mark.asyncio
async def test_unavailable_source_class_matches_protocol_contract():
    src = UnavailableRelationshipObservationSource()
    result = await src.list_observations(owner_id=9, novel_id=9)
    assert result.is_unavailable is True
    assert result.recall_signals()["relationship"]["status"] == "source_unavailable"
