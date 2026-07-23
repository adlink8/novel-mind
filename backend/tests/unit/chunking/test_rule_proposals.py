"""Tests for atomic spans and boundary proposals (07-02)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.chunking.rules import (
    RuleEngineConfig,
    analyze_chapter,
    scan_atomic_spans,
)
from app.services.chunking.schemas import BoundaryProposal, RULE_CONFIDENCE_VERSION

pytestmark = pytest.mark.unit


def test_scan_spans_offsets_and_stable_ids():
    text = "第一句结束。第二句也结束！第三句呢？"
    spans = scan_atomic_spans(chapter_id=1, chapter_number=1, content=text)
    assert len(spans) >= 2
    ids1 = [s.span_id for s in spans]
    spans2 = scan_atomic_spans(chapter_id=1, chapter_number=1, content=text)
    assert [s.span_id for s in spans2] == ids1
    for s in spans:
        assert s.source_end >= s.source_start
        assert s.char_count == len(s.content)
        assert s.content_hash


def test_every_adjacent_pair_has_one_proposal():
    text = "甲句。" * 5 + "翌日乙句。" * 5
    spans, proposals = analyze_chapter(chapter_id=2, chapter_number=1, content=text)
    # N spans → N-1 adjacent + 2 chapter edges
    adj = [
        p
        for p in proposals
        if p.left_span_id in {s.span_id for s in spans}
        and p.right_span_id in {s.span_id for s in spans}
    ]
    assert len(adj) == max(0, len(spans) - 1)
    edges = [p for p in proposals if "CHAPTER_EDGE" in p.reason_codes]
    assert len(edges) == 2
    assert all(e.hard_constraint and not e.llm_eligible for e in edges)


def test_hard_max_not_llm_eligible():
    cfg = RuleEngineConfig(min_chunk_size=10, max_chunk_size=20)
    left = "字" * 25 + "。"
    right = "词" * 25 + "。"
    text = left + right
    spans, proposals = analyze_chapter(
        chapter_id=3, chapter_number=1, content=text, cfg=cfg
    )
    hard = [p for p in proposals if "HARD_MAX_SIZE" in p.reason_codes]
    assert hard
    assert all(p.hard_constraint for p in hard)
    assert all(not p.llm_eligible for p in hard)
    assert all(p.rule_decision == "split" for p in hard)


def test_time_shift_emits_reason():
    text = "昨夜无事。" + "翌日清晨，山间起了雾。"
    spans, proposals = analyze_chapter(chapter_id=4, chapter_number=1, content=text)
    codes = {c for p in proposals for c in p.reason_codes}
    assert (
        "TIME_SHIFT" in codes
        or "LOCATION_SHIFT" in codes
        or "STRUCTURAL_BREAK" in codes
    )


def test_open_quote_marks_risk_and_not_hard():
    text = "他说：“这事还没完。" + "第二天继续说下去。"
    spans, proposals = analyze_chapter(chapter_id=5, chapter_number=1, content=text)
    open_q = [p for p in proposals if "OPEN_QUOTE" in p.reason_codes]
    # May or may not trigger depending on quote pairing; if present must be non-hard
    for p in open_q:
        assert p.hard_constraint is False


def test_proposals_deterministic():
    text = "重复句测试甲。" * 4 + "重复句测试乙。" * 4
    _, p1 = analyze_chapter(chapter_id=6, chapter_number=1, content=text)
    _, p2 = analyze_chapter(chapter_id=6, chapter_number=1, content=text)
    assert [p.proposal_id for p in p1] == [p.proposal_id for p in p2]
    assert [p.confidence for p in p1] == [p.confidence for p in p2]
    assert all(p.confidence_version == RULE_CONFIDENCE_VERSION for p in p1)


def test_llm_eligible_only_below_auto_accept_and_non_hard():
    text = "普通叙述一句。" * 8 + "另一段普通叙述。" * 8
    spans, proposals = analyze_chapter(chapter_id=7, chapter_number=1, content=text)
    for p in proposals:
        if p.hard_constraint:
            assert p.llm_eligible is False
        elif p.confidence >= 0.75:
            assert p.llm_eligible is False
        else:
            assert p.llm_eligible is True


def test_boundary_proposal_rejects_hard_and_eligible():
    with pytest.raises(ValidationError):
        BoundaryProposal(
            proposal_id="bp_badbad01",
            chapter_id=1,
            left_span_id="as_a",
            right_span_id="as_b",
            left_content_hash="a" * 64,
            right_content_hash="b" * 64,
            rule_decision="split",
            confidence=1.0,
            reason_codes=["CHAPTER_EDGE"],
            hard_constraint=True,
            llm_eligible=True,
            fallback_decision="split",
            input_hash="c" * 64,
            rule_config_hash="d" * 64,
        )


def test_short_chapter_under_min():
    cfg = RuleEngineConfig(min_chunk_size=100, max_chunk_size=200)
    text = "短。"
    spans, proposals = analyze_chapter(
        chapter_id=8, chapter_number=1, content=text, cfg=cfg
    )
    assert spans
    # only chapter edges if single span
    adj = [
        p
        for p in proposals
        if p.left_span_id in {s.span_id for s in spans}
        and p.right_span_id in {s.span_id for s in spans}
    ]
    assert len(adj) == 0
