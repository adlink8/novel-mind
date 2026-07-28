"""Phase 19-01: machine clue titles are short hypotheses, not raw cue excerpts."""

from __future__ import annotations

import pytest

from app.schemas.clue import ClueVisibleItem, ClueLifecycleState
from app.services.clues.worker import build_machine_clue_title

pytestmark = pytest.mark.unit


def test_title_prefers_rationale_first_line_not_raw_cue():
    long_cue = "这是一段很长的原文伏笔描写，角色在密室中发现了古老的封印裂痕，" * 3
    title = build_machine_clue_title(
        rationale="封印裂痕可能指向先祖秘钥。\n后续细节待确认。",
        cue_text=long_cue,
        chapter=3,
        candidate_id="clue-abc",
    )
    assert title != long_cue[:80]
    assert long_cue[:40] not in title
    assert "封印裂痕" in title
    assert len(title) <= 32


def test_title_falls_back_to_chapter_stem_when_no_rationale():
    long_cue = "密室中发现了古老的封印裂痕，旁人皆不知其意。"
    title = build_machine_clue_title(
        rationale="",
        cue_text=long_cue,
        chapter=7,
        candidate_id="clue-xyz",
    )
    assert title.startswith("伏笔·第7章")
    assert title != long_cue[:80]
    assert len(title) <= 32


def test_title_never_equals_raw_long_excerpt():
    raw = "A" * 120
    title = build_machine_clue_title(
        rationale=None,
        cue_text=raw,
        chapter=1,
        candidate_id="c1",
    )
    assert title != raw[:80]
    assert len(title) <= 32


def test_visible_item_accepts_span_and_summary_fields():
    item = ClueVisibleItem(
        logical_clue_id="clue-1",
        title="伏笔·第2章·封印",
        derived_state=ClueLifecycleState.ACTIVE,
        narrative_chapter_number=2,
        source_start=0,
        confidence=0.7,
        evidence_count=1,
        link_count=0,
        first_cue_chapter=2,
        payoff_chapter=None,
        summary="短摘要一行",
    )
    assert item.first_cue_chapter == 2
    assert item.payoff_chapter is None
    assert item.summary == "短摘要一行"
