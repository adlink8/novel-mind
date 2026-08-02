"""Shared retrieval-layer and Reader Chat source-priority contracts.

The registry is intentionally small: raw chunks and Narrative Unit are the
production retrieval layers, while Narrative Memory remains candidate-only.
Reader Chat consumes evidence projections, so its source ordering is kept in
the same policy module instead of being duplicated by individual consumers.
"""

from __future__ import annotations

RETRIEVAL_LAYERS: dict[str, str] = {
    "chunks": "enabled",
    "units": "enabled",
    # Candidate-only by policy; enabling this is a Phase 30 authorization event.
    "narrative_memory": "disabled",
}

READER_CHAT_SOURCE_PRIORITY: dict[str, int] = {
    "selection": 0,
    "hierarchy": 1,
    "knowledge": 2,
    "timeline": 3,
    "relationship_observation": 4,
}


def reader_chat_source_priority(source_type: str) -> int:
    """Return the deterministic priority for a supported Reader Chat source."""

    try:
        return READER_CHAT_SOURCE_PRIORITY[source_type]
    except KeyError as exc:
        raise ValueError(f"unsupported reader chat source: {source_type}") from exc


def production_layer_enabled(layer: str) -> bool:
    """Return whether a layer may be selected by production retrieval."""

    return RETRIEVAL_LAYERS.get(layer) == "enabled"


__all__ = [
    "READER_CHAT_SOURCE_PRIORITY",
    "RETRIEVAL_LAYERS",
    "production_layer_enabled",
    "reader_chat_source_priority",
]
