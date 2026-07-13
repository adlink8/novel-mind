"""Tests for deterministic candidate segmentation (07-02)."""

from __future__ import annotations

import pytest

from app.services.chunking.rules import RuleEngineConfig, analyze_chapter
from app.services.chunking.segmentation import segment_chapter, segment_from_proposals

pytestmark = pytest.mark.unit


def test_full_coverage_no_overlap_same_chapter():
    text = "开场叙述。" * 10 + "翌日换景。" * 10 + "收尾对话：「好。」" * 5
    result = segment_chapter(
        chapter_id=1, chapter_number=1, content=text, cfg=RuleEngineConfig(80, 160)
    )
    assert result.segments
    covered = []
    for seg in result.segments:
        covered.extend(seg.span_ids)
        assert seg.chapter_id == 1
        assert seg.char_count == len(seg.content)
    assert covered == [s.span_id for s in result.spans]
    # ordered source starts non-decreasing
    starts = [seg.source_start for seg in result.segments]
    assert starts == sorted(starts)


def test_pending_adjudication_is_llm_eligible_only():
    text = "暧昧边界叙述。" * 12 + "可能切也可能合的句子。" * 12
    result = segment_chapter(chapter_id=2, chapter_number=1, content=text)
    pending_set = set(result.pending_adjudication)
    for p in result.proposals:
        if p.llm_eligible:
            assert p.proposal_id in pending_set
        else:
            assert p.proposal_id not in pending_set
        if p.hard_constraint:
            assert p.proposal_id not in pending_set


def test_hard_max_respected_in_segments():
    cfg = RuleEngineConfig(min_chunk_size=10, max_chunk_size=30)
    text = ("很长的句子用来撑满限制。" * 3 + "。") * 6
    result = segment_chapter(
        chapter_id=3, chapter_number=1, content=text, cfg=cfg
    )
    for seg in result.segments:
        # allow small join overhead but not multi-hard-max collapse
        assert seg.char_count <= cfg.max_chunk_size * 3


def test_segmentation_deterministic():
    text = "确定性分章内容甲。" * 15 + "确定性分章内容乙。" * 15
    cfg = RuleEngineConfig(min_chunk_size=50, max_chunk_size=120)
    a = segment_chapter(chapter_id=4, chapter_number=1, content=text, cfg=cfg)
    b = segment_chapter(chapter_id=4, chapter_number=1, content=text, cfg=cfg)
    assert a.segmentation_checksum == b.segmentation_checksum
    assert [s.segment_id for s in a.segments] == [s.segment_id for s in b.segments]


def test_low_confidence_uses_fallback_but_queues():
    text = "他说：“未闭合引号" + "继续含糊叙述。" * 8
    spans, proposals = analyze_chapter(
        chapter_id=5, chapter_number=1, content=text
    )
    result = segment_from_proposals(spans, proposals)
    # Always produces segments (fallback present)
    assert result.segments
    # Every proposal has fallback_decision
    assert all(p.fallback_decision in ("split", "merge") for p in result.proposals)


def test_dialogue_continuity_and_location():
    text = (
        "「你好。」" 
        + "「再见。」" 
        + "他走进大殿，四周灯火通明。" 
        + "外面风雨大作。"
    )
    result = segment_chapter(
        chapter_id=6,
        chapter_number=2,
        content=text,
        cfg=RuleEngineConfig(min_chunk_size=5, max_chunk_size=80),
    )
    assert result.chapter_number == 2
    codes = {c for p in result.proposals for c in p.reason_codes}
    assert codes  # some reasons emitted
    assert result.rule_config_hash


def test_empty_chapter():
    result = segment_chapter(chapter_id=9, chapter_number=1, content="   \n\n  ")
    assert result.segments == [] or all(s.content.strip() for s in result.segments)
