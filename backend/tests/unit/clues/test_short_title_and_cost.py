"""Phase 25-01: judge short_title priority/fallback and real cost settlement."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.clue import ClueSemanticJudgment
from app.services.clues.evidence import (
    build_clue_evidence_package,
    make_clue_evidence_unit,
)
from app.services.clues.llm_judge import ClueLLMJudgeService
from app.services.clues.worker import (
    COST_REASON_UNKNOWN_PRICING,
    TITLE_SOURCE_JUDGE_SHORT_TITLE,
    TITLE_SOURCE_RATIONALE_OR_STEM,
    compute_actual_cost_usd,
    resolve_machine_clue_title,
)

pytestmark = pytest.mark.unit

HEX64_A = "a" * 64
HEX64_B = "b" * 64


def _package(**overrides: Any):
    cue = make_clue_evidence_unit(
        evidence_id="ev-cue",
        chapter_id=1,
        narrative_chapter_number=1,
        text="Alice found a silver key under the ash gate.",
        role_hint="cue",
    )
    later = make_clue_evidence_unit(
        evidence_id="ev-later",
        chapter_id=5,
        narrative_chapter_number=5,
        text="Alice unlocked the vault with the silver key.",
        role_hint="later",
    )
    kwargs = dict(
        owner_id=1,
        novel_id=2,
        candidate_id="clue-cand-demo",
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        cue_units=[cue],
        later_units=[later],
        recall_signals={},
    )
    kwargs.update(overrides)
    return build_clue_evidence_package(**kwargs)


def _judgment(package, **overrides: Any) -> dict[str, Any]:
    payload = {
        "schema_version": "clue-semantic-judgment.v1",
        "candidate_id": package.candidate_id,
        "classification": "payoff",
        "cue_evidence_ids": package.cue_ids(),
        "later_evidence_ids": package.later_ids()[:1],
        "confidence": 0.9,
        "conflict_flags": [],
        "short_title": "银钥匙与密室封印",
        "rationale": "later text resolves the earlier key cue",
    }
    payload.update(overrides)
    return payload


class FakeTransport:
    def __init__(self, contents: list[str | dict[str, Any]]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._contents = list(contents)

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        content = self._contents.pop(0)
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 16_000, "completion_tokens": 400},
        }


# ---------------------------------------------------------------------------
# short_title schema
# ---------------------------------------------------------------------------


def test_schema_accepts_optional_short_title_and_limits_length():
    package = _package()
    parsed = ClueSemanticJudgment.model_validate(_judgment(package))
    assert parsed.short_title == "银钥匙与密室封印"

    # Missing short_title still validates (older stored/cached outputs).
    legacy = _judgment(package)
    legacy.pop("short_title")
    assert ClueSemanticJudgment.model_validate(legacy).short_title is None

    with pytest.raises(ValidationError):
        ClueSemanticJudgment.model_validate(_judgment(package, short_title="超" * 41))


# ---------------------------------------------------------------------------
# title priority and fallback
# ---------------------------------------------------------------------------


def test_title_prefers_judge_short_title_and_records_source():
    title, source = resolve_machine_clue_title(
        short_title="  银钥匙 与密室封印  ",
        rationale="封印裂痕可能指向先祖秘钥。",
        cue_text="很长的原文……",
        chapter=3,
        candidate_id="c1",
    )
    assert title == "银钥匙 与密室封印"
    assert source == TITLE_SOURCE_JUDGE_SHORT_TITLE
    assert len(title) <= 40


@pytest.mark.parametrize("missing", [None, "", "   ", "x"])
def test_title_falls_back_to_rationale_or_chapter_stem(missing):
    title, source = resolve_machine_clue_title(
        short_title=missing,
        rationale="封印裂痕可能指向先祖秘钥。\n后续待确认。",
        cue_text="密室中发现了古老的封印裂痕。",
        chapter=3,
        candidate_id="c1",
    )
    assert source == TITLE_SOURCE_RATIONALE_OR_STEM
    assert "封印裂痕" in title
    assert len(title) <= 32


def test_title_fallback_without_rationale_uses_chapter_stem():
    title, source = resolve_machine_clue_title(
        short_title=None,
        rationale="",
        cue_text="密室中发现了古老的封印裂痕，旁人皆不知其意。",
        chapter=7,
        candidate_id="clue-xyz",
    )
    assert source == TITLE_SOURCE_RATIONALE_OR_STEM
    assert title.startswith("伏笔·第7章")


def test_overlong_judge_short_title_is_clipped_not_rejected():
    long_title = "线索" * 30
    title, source = resolve_machine_clue_title(
        short_title=long_title,
        rationale=None,
        cue_text=None,
        chapter=None,
        candidate_id="c2",
    )
    assert source == TITLE_SOURCE_JUDGE_SHORT_TITLE
    assert len(title) <= 40
    assert title.endswith("…")


# ---------------------------------------------------------------------------
# judge pass-through (structured + audit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_package_passes_short_title_through_audit():
    package = _package()
    transport = FakeTransport([_judgment(package)])
    service = ClueLLMJudgeService(chat_fn=transport, model_name="test/clue")
    result = await service.judge_package(package)
    assert result.ok
    assert result.structured is not None
    assert result.structured.short_title == "银钥匙与密室封印"
    assert result.audit.short_title == "银钥匙与密室封印"


@pytest.mark.asyncio
async def test_judge_package_audit_short_title_none_when_absent():
    package = _package()
    legacy = _judgment(package)
    legacy.pop("short_title")
    transport = FakeTransport([legacy])
    service = ClueLLMJudgeService(chat_fn=transport, model_name="test/clue")
    result = await service.judge_package(package)
    assert result.ok
    assert result.audit.short_title is None


# ---------------------------------------------------------------------------
# real cost settlement
# ---------------------------------------------------------------------------


def test_actual_cost_is_nonzero_from_usage_times_price_snapshot():
    cost, reason = compute_actual_cost_usd(
        input_tokens=16_000,
        output_tokens=500,
        input_price_per_million=Decimal("0.10"),
        output_price_per_million=Decimal("0.40"),
    )
    assert reason is None
    # 16000 * 0.10/1M + 500 * 0.40/1M = 0.0016 + 0.0002
    assert cost == Decimal("0.0018")
    assert cost > 0


def test_missing_price_snapshot_settles_zero_with_explicit_reason():
    cost, reason = compute_actual_cost_usd(
        input_tokens=16_000,
        output_tokens=500,
        input_price_per_million=None,
        output_price_per_million=Decimal("0.40"),
    )
    assert cost == Decimal("0")
    assert reason == COST_REASON_UNKNOWN_PRICING

    cost2, reason2 = compute_actual_cost_usd(
        input_tokens=1,
        output_tokens=1,
        input_price_per_million=Decimal("0.10"),
        output_price_per_million=None,
    )
    assert cost2 == Decimal("0")
    assert reason2 == COST_REASON_UNKNOWN_PRICING


def test_zero_usage_settles_zero_without_pricing_reason():
    cost, reason = compute_actual_cost_usd(
        input_tokens=0,
        output_tokens=0,
        input_price_per_million=Decimal("0.10"),
        output_price_per_million=Decimal("0.40"),
    )
    assert cost == Decimal("0")
    assert reason is None
