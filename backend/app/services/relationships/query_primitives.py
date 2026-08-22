"""Shared pure primitives for the relationship graph query facade (leaf).

Extracted from ``query.py`` (refactor split): module-level helpers used by the
query facade and its fold mixin without importing either — the D-22 caps
constants, the folded-edge carrier dataclass, the logical relationship key, and
chapter/narrative position comparison. Leaf by construction: imports only
stdlib + ``app.schemas.relationship``, never ``query.py``. The query facade
re-exports these names so the ``app.services.relationships.query`` import
surface is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.relationship import (
    ProvenanceKind,
    RelationshipEdgeKind,
    RelationshipIntakeKind,
)

# D-22 degradation thresholds (immutable product contract).
NORMAL_NODE_CAP = 200
NORMAL_EDGE_CAP = 600
HARD_NODE_CAP = 500
HARD_EDGE_CAP = 1500


@dataclass
class _FoldedEdge:
    observation_id: int
    source_character_id: int
    target_character_id: int
    relation_type: str
    transition: str
    confidence: float
    valid_from_chapter: int
    valid_to_chapter: int | None
    logical_key: str
    provenance: ProvenanceKind = ProvenanceKind.MACHINE
    evidence_preview: str | None = None
    evidence_count: int = 0
    edge_kind: RelationshipEdgeKind = RelationshipEdgeKind.ACCEPTED_OBSERVATION
    suggested_type: str | None = None
    intake_kind: str = RelationshipIntakeKind.UNKNOWN.value


def logical_relationship_key(
    source_character_id: int,
    target_character_id: int,
    relation_type: str,
) -> str:
    """Stable directed key used by overrides and fold grouping."""
    return f"{source_character_id}:{target_character_id}:{relation_type}"


def _position_tuple(chapter: int, narrative_index: int = 0) -> tuple[int, int]:
    return (chapter, narrative_index)


def _covers_position(
    *,
    valid_from_chapter: int,
    valid_from_narrative_index: int,
    valid_to_chapter: int | None,
    valid_to_narrative_index: int | None,
    through_chapter: int,
    through_narrative_index: int = 0,
) -> bool:
    start = _position_tuple(valid_from_chapter, valid_from_narrative_index)
    pos = _position_tuple(through_chapter, through_narrative_index)
    if start > pos:
        return False
    if valid_to_chapter is None:
        return True
    end = _position_tuple(valid_to_chapter, valid_to_narrative_index or 0)
    return end >= pos


__all__ = [
    "HARD_EDGE_CAP",
    "HARD_NODE_CAP",
    "NORMAL_EDGE_CAP",
    "NORMAL_NODE_CAP",
    "_FoldedEdge",
    "_covers_position",
    "_position_tuple",
    "logical_relationship_key",
]
