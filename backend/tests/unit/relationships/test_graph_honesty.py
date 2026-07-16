"""Phase 19-01: provisional co-occurrence honesty on graph projection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.relationship import (
    ProvenanceKind,
    RelationshipEdgeKind,
    RelationshipEdgeType,
    RelationshipGraphEdge,
    RelationshipGraphEdgeLabel,
)
from app.services.relationships.query import RelationshipGraphQueryService, _FoldedEdge

pytestmark = pytest.mark.unit


def test_provisional_edge_schema_requires_cooccur_label():
    edge = RelationshipGraphEdge(
        observation_id=1,
        source_character_id=1,
        target_character_id=2,
        relation_type=RelationshipGraphEdgeLabel.COOCCUR,
        transition="establish",
        confidence=0.4,
        valid_from_chapter=1,
        valid_to_chapter=None,
        provenance=ProvenanceKind.MACHINE,
        evidence_preview="时间线共现×3（类型线索·同盟/协作×2，非已确认关系） · 临时图",
        evidence_count=3,
        edge_kind=RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE,
        suggested_type=RelationshipEdgeType.ALLY,
    )
    assert edge.relation_type == RelationshipGraphEdgeLabel.COOCCUR
    assert edge.edge_kind == RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE
    assert edge.suggested_type == RelationshipEdgeType.ALLY
    assert "共现" in (edge.evidence_preview or "")
    assert "同盟×" not in (edge.evidence_preview or "")


def test_provisional_edge_rejects_fake_ally_primary_label():
    with pytest.raises(ValidationError):
        RelationshipGraphEdge(
            observation_id=1,
            source_character_id=1,
            target_character_id=2,
            relation_type=RelationshipGraphEdgeLabel.ALLY,
            transition="establish",
            confidence=0.4,
            valid_from_chapter=1,
            edge_kind=RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE,
            suggested_type=RelationshipEdgeType.ALLY,
        )


def test_accepted_edge_rejects_cooccur_label():
    with pytest.raises(ValidationError):
        RelationshipGraphEdge(
            observation_id=1,
            source_character_id=1,
            target_character_id=2,
            relation_type=RelationshipGraphEdgeLabel.COOCCUR,
            transition="establish",
            confidence=0.9,
            valid_from_chapter=1,
            edge_kind=RelationshipEdgeKind.ACCEPTED_OBSERVATION,
        )


def test_folded_provisional_preview_is_cooccurrence_not_asserted_ally():
    """Unit-level contract for provisional preview wording + labels."""

    edge = _FoldedEdge(
        observation_id=99,
        source_character_id=1,
        target_character_id=2,
        relation_type=RelationshipGraphEdgeLabel.COOCCUR.value,
        transition="establish",
        confidence=0.4,
        valid_from_chapter=2,
        valid_to_chapter=None,
        logical_key="1:2:cooccur",
        provenance=ProvenanceKind.MACHINE,
        evidence_preview=(
            "时间线共现×4（类型线索·敌对/冲突×3，非已确认关系）：对峙 · 临时图"
        ),
        evidence_count=4,
        edge_kind=RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE,
        suggested_type=RelationshipEdgeType.ENEMY.value,
    )
    assert edge.relation_type == "cooccur"
    assert edge.edge_kind == RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE
    assert edge.suggested_type == "enemy"
    preview = edge.evidence_preview or ""
    assert "共现" in preview
    assert "非已确认关系" in preview
    # Must not present fiction enum count as sole factual claim like 同盟×N.
    assert not preview.startswith("时间线推断·同盟")


def test_infer_provisional_type_still_heuristic_for_suggested_only():
    """Heuristics remain available for suggested_type / seed backfill."""

    infer = RelationshipGraphQueryService._infer_provisional_type
    assert (
        infer(title="对峙", description="双方僵持", event_type="conflict") == "enemy"
    )
    assert (
        infer(title="会谈", description="平静交谈", event_type="character") == "ally"
    )
