"""Deterministic, fail-closed QueryPlan parser (Phase 26-01 / REQ-QP-01).

Rules applied in fixed order, each returning a stable ``BlockedResult`` on
failure (D-02/D-03/D-12):

1. strict schema validation (unknown intent, malformed scope, contradictory
   answer constraints → blocked; never guessed)
2. whole-book gate: explicit switch required, and the per-novel authorization
   must be true (D-12)
3. future probing / scope escape: question chapter references and anchor bounds
   must stay within the reading-progress cutoff
4. anchor-intent coherence (D-10): reader ↔ selection, analysis ↔ chapter_range

The parser is pure — on any failure it returns only a stable blocked reason and
creates no trace and writes nothing to the database.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.services.queryplan.schemas import (
    Anchor,
    AnswerConstraints,
    AvailabilityStatus,
    BlockedReasonCode,
    BlockedResult,
    CutoffMode,
    DimensionAvailability,
    FallbackPolicy,
    QueryDimension,
    QueryPlan,
    QueryPlanIntent,
    QueryPlanRequest,
    QueryPlanTrace,
    QUERYPLAN_DATASET_VERSION,
    QUERYPLAN_PARSER_VERSION,
    QUERYPLAN_SCHEMA_VERSION,
    SpoilerCutoff,
    availability_checksum,
    idempotency_key,
    normalize_query_text,
    plan_payload_hash,
    question_hash,
)

_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHAPTER_REF_DIGIT_RE = re.compile(
    r"(?:第\s*)?([0-9]{1,4})\s*(?:章|话|节|回)", re.IGNORECASE
)
_CHAPTER_REF_CN_RE = re.compile(
    r"第\s*([0-9一二三四五六七八九十百零]+)\s*(?:章|话|节|回)"
)
_CHAPTER_REF_EN_RE = re.compile(r"\b(?:chapter|ch\.?)\s*([0-9]{1,4})\b", re.IGNORECASE)
# Strong, unambiguous whole-book wording. Ambiguous analysis words such as 主线 /
# 主题 / theme are deliberately excluded: an explicit chapter_range anchor scopes
# the question, and a false whole-book hit would wrongly block a scoped analysis.
_WHOLE_BOOK_SIGNAL_RE = re.compile(
    r"全书|整本|整部|整个故事|整体|全局|总览|从头到尾|"
    r"\bwhole book\b|\bentire (?:novel|book|story)\b|"
    r"\bfrom start to (?:finish|end)\b|\boverall\b|\bglobal\b",
    re.IGNORECASE,
)

_DEFAULT_DIMENSIONS: dict[QueryPlanIntent, tuple[QueryDimension, ...]] = {
    QueryPlanIntent.READER: (
        QueryDimension.RAW_TEXT,
        QueryDimension.EVENTS_CAUSALITY,
        QueryDimension.CHARACTER_STATE,
        QueryDimension.RELATIONS,
        QueryDimension.TIMELINE,
        QueryDimension.CLUES_FORESHAOWING,
    ),
    QueryPlanIntent.ANALYSIS: (
        QueryDimension.EVENTS_CAUSALITY,
        QueryDimension.CHARACTER_STATE,
        QueryDimension.RELATIONS,
        QueryDimension.TIMELINE,
        QueryDimension.CLUES_FORESHAOWING,
        QueryDimension.WORLD_RULES,
        QueryDimension.NARRATIVE_UNITS,
    ),
}

# Production readers are absent until Phase 27 (RESEARCH verified). Such
# dimensions are declared unavailable with a stable reason — never empty-success
# (D-05). Heuristic candidate recall has no fact / EvidenceRef / citation
# eligibility (D-15).
_ABSENT_READERS: dict[QueryDimension, str] = {
    QueryDimension.CHARACTER_STATE: "character_state_reader_not_implemented_phase27",
    QueryDimension.WORLD_RULES: "world_rules_reader_not_implemented_phase27",
}


def _cn_numeral_to_int(text: str) -> int | None:
    """Convert a Chinese numeral chapter reference (十/二十/一百零三) to int."""

    total = 0
    current = 0
    for char in text:
        if char == "零":
            continue
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        elif char == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


def _referenced_chapters(text: str) -> set[int]:
    refs: set[int] = set()
    for match in _CHAPTER_REF_DIGIT_RE.finditer(text):
        refs.add(int(match.group(1)))
    for match in _CHAPTER_REF_EN_RE.finditer(text):
        refs.add(int(match.group(1)))
    for match in _CHAPTER_REF_CN_RE.finditer(text):
        raw = match.group(1)
        if raw.isdigit():
            refs.add(int(raw))
        else:
            value = _cn_numeral_to_int(raw)
            if value is not None and value > 0:
                refs.add(value)
    return refs


def _blocked(code: BlockedReasonCode, message: str, clarification: str) -> BlockedResult:
    return BlockedResult(reason_code=code, message=message, clarification=clarification)


def _blocked_from_validation_error(exc: ValidationError) -> BlockedResult:
    """Map a strict schema failure to a stable blocked reason."""

    for err in exc.errors():
        loc = list(err.get("loc", ()))
        msg = str(err.get("msg", ""))
        if "intent" in loc:
            return _blocked(
                BlockedReasonCode.UNKNOWN_INTENT,
                f"unknown or missing intent: {msg}",
                "请将意图指定为 reader 或 analysis 后再提问。",
            )
        if "answer_constraints" in loc and "uncited-fact" in msg:
            return _blocked(
                BlockedReasonCode.CONTRADICTORY,
                "answer constraints are contradictory: both citation and abstention "
                "relaxed at once",
                "每个事实必须引用证据，或证据不足时拒绝作答；两者不能同时关闭。",
            )
    return _blocked(
        BlockedReasonCode.INVALID_INPUT,
        "request payload failed strict validation",
        "请检查问题、范围与约束字段的取值。",
    )


def _check_whole_book(request: QueryPlanRequest) -> BlockedResult | None:
    if request.whole_book:
        if not request.reading_progress.full_book_authorized:
            return _blocked(
                BlockedReasonCode.CONTRADICTORY,
                "whole_book requested but the novel does not authorize full-book "
                "reading (D-12)",
                "需要先为该小说启用 whole-book 权限，或改用 reading-progress 范围。",
            )
        return None
    if _WHOLE_BOOK_SIGNAL_RE.search(request.question_text):
        return _blocked(
            BlockedReasonCode.WHOLE_BOOK_UNAUTHORIZED,
            "whole-book wording requires the explicit whole_book switch (D-12)",
            "整本书范围需要显式开启 whole_book 开关，或将问题限定在已读章节内。",
        )
    return None


def _check_future_probing(request: QueryPlanRequest) -> BlockedResult | None:
    if request.whole_book:
        # Whole-book scope legitimately covers every chapter.
        return None
    cutoff = request.reading_progress.through_chapter
    future = sorted(c for c in _referenced_chapters(request.question_text) if c > cutoff)
    if future:
        return _blocked(
            BlockedReasonCode.FUTURE_PROBING,
            f"question references chapter(s) beyond the reading cutoff: {future}",
            "请将问题限制在已读到章节（reading progress）之内。",
        )
    if request.selection is not None and request.selection.chapter_id > cutoff:
        return _blocked(
            BlockedReasonCode.SCOPE_ESCAPE,
            f"selection chapter {request.selection.chapter_id} exceeds cutoff {cutoff}",
            "选中的章节超出当前阅读进度。",
        )
    if (
        request.chapter_range is not None
        and request.chapter_range.chapter_end > cutoff
    ):
        return _blocked(
            BlockedReasonCode.SCOPE_ESCAPE,
            f"chapter_range end {request.chapter_range.chapter_end} exceeds cutoff {cutoff}",
            "分析范围超出当前阅读进度。",
        )
    return None


def _check_anchor_intent(request: QueryPlanRequest) -> BlockedResult | None:
    has_selection = request.selection is not None
    has_range = request.chapter_range is not None
    if has_selection and has_range:
        return _blocked(
            BlockedReasonCode.AMBIGUOUS_INTENT,
            "both selection and chapter_range anchors provided",
            "请只提供一种锚点：reader 用 selection，analysis 用 chapter_range。",
        )
    if request.intent == QueryPlanIntent.READER:
        if has_range:
            return _blocked(
                BlockedReasonCode.AMBIGUOUS_INTENT,
                "reader intent cannot carry a chapter_range anchor (D-10)",
                "读者问题请使用 selection 或单章上下文，而不是分析范围。",
            )
        return None
    # analysis
    if has_selection:
        return _blocked(
            BlockedReasonCode.AMBIGUOUS_INTENT,
            "analysis intent cannot carry a selection anchor (D-10)",
            "分析问题请使用 chapter_range 结构锚点。",
        )
    if not has_range:
        return _blocked(
            BlockedReasonCode.AMBIGUOUS_INTENT,
            "analysis intent requires a chapter_range anchor",
            "分析问题需要指定分析范围（chapter_range）。",
        )
    return None


def _resolve_dimensions(request: QueryPlanRequest) -> tuple[QueryDimension, ...]:
    if request.dimensions is not None:
        return tuple(dict.fromkeys(request.dimensions))
    return _DEFAULT_DIMENSIONS[request.intent]


def _resolve_availability(
    dimensions: tuple[QueryDimension, ...],
) -> tuple[DimensionAvailability, ...]:
    entries: list[DimensionAvailability] = []
    for dimension in dimensions:
        absent_reason = _ABSENT_READERS.get(dimension)
        if absent_reason is not None:
            entries.append(
                DimensionAvailability(
                    dimension=dimension,
                    status=AvailabilityStatus.UNAVAILABLE,
                    reason=absent_reason,
                    provenance="no_production_reader",
                )
            )
        else:
            entries.append(
                DimensionAvailability(
                    dimension=dimension,
                    status=AvailabilityStatus.AVAILABLE,
                    reason="reader_expected",
                    provenance="deterministic_contract_v1",
                )
            )
    return tuple(entries)


def _new_trace(
    request: QueryPlanRequest,
    *,
    payload_hash: str,
    avail_checksum: str,
) -> QueryPlanTrace:
    return QueryPlanTrace(
        trace_id=uuid4().hex,
        idempotency_key=idempotency_key(request),
        schema_version=QUERYPLAN_SCHEMA_VERSION,
        parser_version=QUERYPLAN_PARSER_VERSION,
        created_at=datetime.now(timezone.utc),
        source=request.source,
        dataset_lineage=request.dataset_lineage,
        canonical_payload_hash=payload_hash,
        availability_checksum=avail_checksum,
    )


def _build_plan(
    request: QueryPlanRequest,
    dimensions: tuple[QueryDimension, ...],
    availability: tuple[DimensionAvailability, ...],
) -> QueryPlan:
    normalized = normalize_query_text(request.question_text)
    whole_book = request.whole_book
    cutoff = SpoilerCutoff(
        mode=CutoffMode.WHOLE_BOOK if whole_book else CutoffMode.READING_PROGRESS,
        through_chapter=request.reading_progress.through_chapter,
        full_book_authorized=(
            request.reading_progress.full_book_authorized if whole_book else False
        ),
    )
    anchor: Anchor | None = (
        request.selection if request.selection is not None else request.chapter_range
    )
    dummy_trace = _new_trace(
        request, payload_hash="0" * 64, avail_checksum="0" * 64
    )
    base = QueryPlan(
        intent=request.intent,
        owner_id=request.owner_id,
        novel_id=request.novel_id,
        version_id=request.version_id,
        spoiler_cutoff=cutoff,
        dimensions=dimensions,
        availability=availability,
        fallback=FallbackPolicy(),
        answer_constraints=request.answer_constraints or AnswerConstraints(),
        anchor=anchor,
        question_hash=question_hash(normalized),
        trace=dummy_trace,
    )
    payload_hash = plan_payload_hash(base.model_dump(mode="json", exclude={"trace"}))
    avail_checksum = availability_checksum(base.availability)
    trace = _new_trace(request, payload_hash=payload_hash, avail_checksum=avail_checksum)
    return base.model_copy(update={"trace": trace})


def parse_query_plan(payload: dict[str, Any]) -> QueryPlan | BlockedResult:
    """Deterministic fail-closed parse. Pure: no trace, no database writes."""

    try:
        request = QueryPlanRequest.model_validate(payload)
    except ValidationError as exc:
        return _blocked_from_validation_error(exc)

    blocked = _check_whole_book(request)
    if blocked is not None:
        return blocked
    blocked = _check_future_probing(request)
    if blocked is not None:
        return blocked
    blocked = _check_anchor_intent(request)
    if blocked is not None:
        return blocked

    dimensions = _resolve_dimensions(request)
    availability = _resolve_availability(dimensions)
    return _build_plan(request, dimensions, availability)
