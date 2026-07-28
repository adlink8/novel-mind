"""Unit tests for carry-forward helpers and executor mask."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.narrative_memory.carry_forward import carry_has_provider_capability
from app.services.narrative_memory.rebuild_executor import (
    build_dirty_stage_mask,
    executor_has_provider_capability,
)
from app.services.narrative_memory.rebuild_contracts import RebuildDecision

pytestmark = pytest.mark.unit


def test_carry_and_executor_provider_free() -> None:
    assert carry_has_provider_capability() is False
    assert executor_has_provider_capability() is False


def test_dirty_mask_excludes_carried_stages() -> None:
    plan = SimpleNamespace(id=1, plan_checksum="a" * 64)
    items = [
        SimpleNamespace(
            asset_key="chapter_state:1",
            asset_kind="chapter_state",
            decision=RebuildDecision.CARRIED.value,
            stage_key="chapter_state:1",
            chapter_start=1,
            chapter_end=1,
            predecessor_keys=[],
            direct_reasons=[],
        ),
        SimpleNamespace(
            asset_key="chapter_state:2",
            asset_kind="chapter_state",
            decision=RebuildDecision.DIRTY.value,
            stage_key="chapter_state:2",
            chapter_start=2,
            chapter_end=2,
            predecessor_keys=["source_chapter:2"],
            direct_reasons=["chapter_edited"],
        ),
        SimpleNamespace(
            asset_key="global_story:book",
            asset_kind="global_story",
            decision=RebuildDecision.DIRTY.value,
            stage_key="global_story:book",
            chapter_start=1,
            chapter_end=2,
            predecessor_keys=[],
            direct_reasons=[],
        ),
    ]
    mask = build_dirty_stage_mask(plan, items)
    assert "chapter_state:1" not in mask.dirty_stage_keys
    assert "chapter_state:2" in mask.dirty_stage_keys
    assert "global_story:book" in mask.dirty_stage_keys
    assert "chapter_state:1" in mask.carried_asset_keys
    assert all(s["stage_key"] != "chapter_state:1" for s in mask.stage_specs)
