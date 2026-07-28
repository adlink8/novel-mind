"""07-05 incremental delta planner."""

from __future__ import annotations
import pytest
from app.services.chunking.incremental import plan_incremental_delta

pytestmark = pytest.mark.unit


def test_no_op_when_identical():
    # use int keys
    prev = {1: "a" * 64, 2: "b" * 64}
    cur = {1: "a" * 64, 2: "b" * 64}
    lin = {"chunker_name": "h", "chunker_version": "1", "chunker_config_hash": "c" * 64}
    d = plan_incremental_delta(
        prev_chapter_hashes=prev,
        current_chapter_hashes=cur,
        prev_chunker_lineage=lin,
        current_chunker_lineage=lin,
    )
    assert d.no_op is True
    assert d.changed_chapter_ids == []


def test_single_chapter_change():
    prev = {1: "a" * 64, 2: "b" * 64}
    cur = {1: "a" * 64, 2: "d" * 64}
    lin = {"chunker_name": "h", "chunker_version": "1", "chunker_config_hash": "c" * 64}
    d = plan_incremental_delta(
        prev_chapter_hashes=prev,
        current_chapter_hashes=cur,
        prev_chunker_lineage=lin,
        current_chunker_lineage=lin,
    )
    assert d.no_op is False
    assert d.changed_chapter_ids == [2]


def test_chunker_lineage_forces_full():
    prev = {1: "a" * 64}
    cur = {1: "a" * 64}
    d = plan_incremental_delta(
        prev_chapter_hashes=prev,
        current_chapter_hashes=cur,
        prev_chunker_lineage={
            "chunker_name": "a",
            "chunker_version": "1",
            "chunker_config_hash": "c" * 64,
        },
        current_chunker_lineage={
            "chunker_name": "b",
            "chunker_version": "1",
            "chunker_config_hash": "c" * 64,
        },
    )
    assert d.full is True
