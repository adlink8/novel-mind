"""Shared production retrieval and Reader Chat priority contracts (24-04)."""

import pytest

from app.services.knowledge_units.search import RETRIEVAL_LAYERS
from app.services.reader_chat.retrieval import SOURCE_PRIORITY
from app.services.retrieval_policy import (
    READER_CHAT_SOURCE_PRIORITY,
    production_layer_enabled,
    reader_chat_source_priority,
)

pytestmark = pytest.mark.unit


def test_production_registry_keeps_narrative_memory_candidate_only():
    assert RETRIEVAL_LAYERS == {
        "chunks": "enabled",
        "units": "enabled",
        "narrative_memory": "disabled",
    }
    assert production_layer_enabled("chunks")
    assert production_layer_enabled("units")
    assert not production_layer_enabled("narrative_memory")


def test_reader_chat_priority_is_shared_and_stable():
    assert SOURCE_PRIORITY is READER_CHAT_SOURCE_PRIORITY
    assert list(SOURCE_PRIORITY) == [
        "selection",
        "hierarchy",
        "knowledge",
        "timeline",
        "relationship_observation",
    ]
    assert [reader_chat_source_priority(key) for key in SOURCE_PRIORITY] == [
        0,
        1,
        2,
        3,
        4,
    ]


def test_reader_chat_rejects_unknown_source_priority():
    with pytest.raises(ValueError, match="unsupported reader chat source"):
        reader_chat_source_priority("narrative_memory")
