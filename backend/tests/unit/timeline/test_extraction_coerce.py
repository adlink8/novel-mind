"""_coerce_timeline_json_blob 容错契约：模型回填的 hash/extra 字段/时间格式
在 rebind 前置校验处不再产生致命 schema_rejected（rebind 会权威覆写，
validate_extraction 做终审）。"""

import json

import pytest

from app.schemas.timeline import TimelineExtraction
from app.services.timeline.model_gateway import _coerce_timeline_json_blob

pytestmark = pytest.mark.unit


def _validate(raw: str) -> TimelineExtraction:
    return TimelineExtraction.model_validate_json(
        _coerce_timeline_json_blob(raw), strict=False
    )


def _event(**overrides):
    event = {
        "candidate_id": "ev-1",
        "title": "事件",
        "description": "描述",
        "event_type": "plot",
        "narrative_chapter_number": 1936,
        "narrative_index": 0,
        "participants": [{"mention": "角色", "entity_id": None}],
        "story_time": {"precision": "unknown"},
        "evidence": [
            {
                "chapter_id": 1936,
                "evidence_id": "unit-1",
                "source_start": 0,
                "source_end": 10,
                "content_hash": "a" * 64,
            }
        ],
        "confidence": 0.8,
    }
    event.update(overrides)
    return event


def test_extra_fields_are_trimmed_instead_of_rejecting():
    """模型在任意层级多吐字段：coerce 裁剪后应通过，不再 schema_rejected。"""
    raw = json.dumps(
        {
            "events": [
                _event(
                    notes="模型自作主张",
                    participants=[
                        {"mention": "角色", "entity_id": None, "role": "主角"},
                    ],
                    evidence=[
                        {
                            "chapter_id": 1936,
                            "evidence_id": "unit-1",
                            "source_start": 0,
                            "source_end": 10,
                            "content_hash": "a" * 64,
                            "quote": "原文片段",
                        }
                    ],
                )
            ],
            "story_time_constraints": [],
            "unexpected": True,
        },
        ensure_ascii=False,
    )
    output = _validate(raw)
    assert output.events[0].evidence[0].evidence_id == "unit-1"


def test_malformed_content_hash_is_accepted_and_rebound_later():
    """模型抄错/非字符串 hash 不再炸校验：rebind 用包权威值覆写。"""
    raw = json.dumps(
        {
            "events": [
                _event(
                    evidence=[
                        {
                            "chapter_id": 1936,
                            "evidence_id": "unit-1",
                            "source_start": 0,
                            "source_end": 10,
                            "content_hash": "abc123",  # 非 64hex
                        }
                    ]
                )
            ],
            "story_time_constraints": [],
        }
    )
    output = _validate(raw)
    assert output.events[0].evidence[0].content_hash == "abc123"

    raw_null = json.dumps(
        {
            "events": [
                _event(
                    evidence=[
                        {
                            "chapter_id": 1936,
                            "evidence_id": "unit-1",
                            "source_start": 0,
                            "source_end": 10,
                            "content_hash": None,
                        }
                    ]
                )
            ],
            "story_time_constraints": [],
        }
    )
    output_null = _validate(raw_null)
    assert len(output_null.events[0].evidence[0].content_hash) == 64


def test_chinese_datetime_is_normalized_to_iso():
    raw = json.dumps(
        {
            "events": [
                _event(
                    story_time={
                        "precision": "exact",
                        "expression": "1976年1月1日",
                        "exact_time": "1976年1月1日",
                    }
                )
            ],
            "story_time_constraints": [],
        },
        ensure_ascii=False,
    )
    output = _validate(raw)
    assert output.events[0].story_time.exact_time is not None
    assert output.events[0].story_time.exact_time.year == 1976


def test_unparseable_exact_time_downgrades_to_unknown():
    raw = json.dumps(
        {
            "events": [
                _event(
                    story_time={
                        "precision": "exact",
                        "expression": "很久以前",
                        "exact_time": "not-a-date",
                    }
                )
            ],
            "story_time_constraints": [],
        },
        ensure_ascii=False,
    )
    output = _validate(raw)
    assert output.events[0].story_time.precision.value == "unknown"


def test_fuzzy_time_invalid_bound_is_cleared_not_rejected():
    raw = json.dumps(
        {
            "events": [
                _event(
                    story_time={
                        "precision": "fuzzy",
                        "expression": "夏季",
                        "fuzzy_start": "1976-06-01",
                        "fuzzy_end": "某天",
                    }
                )
            ],
            "story_time_constraints": [],
        },
        ensure_ascii=False,
    )
    output = _validate(raw)
    st = output.events[0].story_time
    assert st.precision.value == "fuzzy"
    assert st.fuzzy_start is not None
    assert st.fuzzy_end is None


def test_json_fence_still_stripped():
    raw = "```json\n" + json.dumps(
        {"events": [], "story_time_constraints": []}
    ) + "\n```"
    assert _validate(raw).events == []


def test_conflicting_story_time_fields_are_stripped_not_rejected():
    """真实故障根因：模型残留与 precision 冲突的字段，validate_precision_shape
    直接整章拒绝。coerce 按档位剥离禁止字段。"""
    # unknown 却带 anchor/relation/exact_time
    raw = json.dumps(
        {
            "events": [
                _event(
                    story_time={
                        "precision": "unknown",
                        "expression": "某日",
                        "anchor_event_id": "ev-0",
                        "relation": "after",
                        "exact_time": "1976-01-01",
                    }
                )
            ],
            "story_time_constraints": [],
        },
        ensure_ascii=False,
    )
    output = _validate(raw)
    st = output.events[0].story_time
    assert st.precision.value == "unknown"
    assert st.anchor_event_id is None and st.relation is None
    assert st.exact_time is None

    # exact 却残留 fuzzy_start
    raw_exact = json.dumps(
        {
            "events": [
                _event(
                    story_time={
                        "precision": "exact",
                        "expression": "1976年1月1日",
                        "exact_time": "1976-01-01",
                        "fuzzy_start": "1976-01-01",
                    }
                )
            ],
            "story_time_constraints": [],
        },
        ensure_ascii=False,
    )
    output_exact = _validate(raw_exact)
    st_exact = output_exact.events[0].story_time
    assert st_exact.precision.value == "exact"
    assert st_exact.fuzzy_start is None
    assert st_exact.exact_time is not None


def test_structurally_broken_output_is_still_rejected():
    """coerce 只救可安全修复的偏差；结构性缺失必须照常拒绝。"""
    with pytest.raises(ValueError):
        _validate('{"events": [{"candidate_id": null}]}')
