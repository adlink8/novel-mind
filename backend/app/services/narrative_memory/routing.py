"""Versioned deterministic local/arc/global/mixed routing policy.

The router inspects only normalized question text, optional selection anchors,
persisted cutoff authorization, and fixed pattern tables. It never receives
candidate rows, summaries, node counts, embeddings, or provider access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.narrative_memory.retrieval_contracts import (
    RouteDecision,
    RouteMode,
    RouteReasonCode,
    StartLevel,
    RetrievalQuestion,
    RetrievalScope,
    retrieval_component_hash,
)


ROUTING_POLICY_VERSION = "narrative-memory-routing.v1"

# Precedence-ordered pattern tables (first match within a bucket wins).
# Chinese Unicode preserved; patterns are case-insensitive for ASCII only.
_LOCAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"这[一]?章",
        r"本章",
        r"当前章",
        r"选中",
        r"这段",
        r"此处",
        r"是谁",
        r"在哪里",
        r"在哪",
        r"什么时候",
        r"何时",
        r"状态",
        r"身份",
        r"角色",
        r"\bwho\b",
        r"\bwhere\b",
        r"\bwhen\b",
        r"\bthis chapter\b",
        r"\bselected\b",
        r"\bdefinition\b",
        r"\bentity\b",
    )
)

_ARC_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"为什么",
        r"为何",
        r"怎么变成",
        r"如何演变",
        r"前后",
        r"过渡",
        r"跨章",
        r"因果",
        r"转折",
        r"弧线",
        r"情节线",
        r"从.+到",
        r"\bwhy\b",
        r"\bcause\b",
        r"\barc\b",
        r"\bcross[- ]?chapter\b",
        r"\btransition\b",
        r"\bhow did\b",
    )
)

_GLOBAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"全书",
        r"整本书",
        r"整部",
        r"整体",
        r"主线",
        r"主题",
        r"全局",
        r"总览",
        r"整体轨迹",
        r"\bwhole book\b",
        r"\bentire (?:novel|book|story)\b",
        r"\boverall\b",
        r"\btheme\b",
        r"\bglobal\b",
        r"\bmain plot\b",
    )
)

_NO_ANSWER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^无$",
        r"^没有$",
        r"^不知道$",
        r"^n/?a$",
        r"^none$",
        r"^no answer$",
        r"^无关$",
    )
)

_START_LEVELS: dict[RouteMode, tuple[StartLevel, ...]] = {
    RouteMode.LOCAL: (StartLevel.CHAPTER_STATE,),
    RouteMode.ARC: (StartLevel.STORY_ARC, StartLevel.VOLUME),
    RouteMode.GLOBAL: (StartLevel.GLOBAL_STORY,),
    RouteMode.MIXED: (
        StartLevel.CHAPTER_STATE,
        StartLevel.STORY_ARC,
        StartLevel.VOLUME,
    ),
}


def _policy_payload() -> dict[str, object]:
    return {
        "version": ROUTING_POLICY_VERSION,
        "local": [p.pattern for p in _LOCAL_PATTERNS],
        "arc": [p.pattern for p in _ARC_PATTERNS],
        "global": [p.pattern for p in _GLOBAL_PATTERNS],
        "no_answer": [p.pattern for p in _NO_ANSWER_PATTERNS],
        "start_levels": {
            mode.value: [level.value for level in levels]
            for mode, levels in _START_LEVELS.items()
        },
        "precedence": [
            "selection_local",
            "global_authorized",
            "global_unauthorized_downgrade",
            "arc",
            "local_intent",
            "mixed_multi",
            "safe_default",
        ],
    }


ROUTING_POLICY_HASH: str = retrieval_component_hash("routing-policy", _policy_payload())


@dataclass(frozen=True)
class _Signals:
    has_selection: bool
    local: bool
    arc: bool
    global_: bool
    no_answer: bool


def _match_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)


def _extract_signals(question: RetrievalQuestion) -> _Signals:
    text = question.normalized_text
    has_selection = (
        question.selected_chapter is not None
        or (
            question.selected_start is not None and question.selected_end is not None
        )
    )
    return _Signals(
        has_selection=has_selection,
        local=_match_any(text, _LOCAL_PATTERNS),
        arc=_match_any(text, _ARC_PATTERNS),
        global_=_match_any(text, _GLOBAL_PATTERNS),
        no_answer=_match_any(text, _NO_ANSWER_PATTERNS),
    )


def decide_route(
    question: RetrievalQuestion,
    *,
    full_book_authorized: bool = False,
    policy_version: str = ROUTING_POLICY_VERSION,
    policy_hash: str = ROUTING_POLICY_HASH,
) -> RouteDecision:
    """Return a closed route decision with stable reason codes and start levels.

    Parameters intentionally exclude candidate materialization inputs.
    """

    if policy_version != ROUTING_POLICY_VERSION or policy_hash != ROUTING_POLICY_HASH:
        raise ValueError("unsupported routing policy version/hash")

    signals = _extract_signals(question)
    reasons: list[RouteReasonCode] = []

    if signals.no_answer and not (
        signals.local or signals.arc or signals.global_ or signals.has_selection
    ):
        return RouteDecision(
            mode=RouteMode.MIXED,
            start_levels=_START_LEVELS[RouteMode.MIXED],
            reason_codes=(RouteReasonCode.NO_ANSWER_SHAPE, RouteReasonCode.SAFE_DEFAULT),
            policy_version=policy_version,
            policy_hash=policy_hash,
        )

    # Selection anchor strongly prefers local when not combined with global intent.
    selection_local = signals.has_selection and not signals.global_
    global_hit = signals.global_
    arc_hit = signals.arc
    local_hit = signals.local or selection_local

    signal_count = sum(
        (
            1 if local_hit else 0,
            1 if arc_hit else 0,
            1 if global_hit else 0,
        )
    )

    if selection_local:
        reasons.append(RouteReasonCode.SELECTION_ANCHOR)
    if signals.local:
        reasons.append(RouteReasonCode.LOCAL_FACT_INTENT)
    if arc_hit:
        reasons.append(RouteReasonCode.CROSS_CHAPTER_INTENT)
    if global_hit:
        reasons.append(RouteReasonCode.WHOLE_BOOK_INTENT)

    # Global wording without authorization must not widen scope.
    if global_hit and not full_book_authorized:
        reasons.append(RouteReasonCode.UNAUTHORIZED_GLOBAL)
        if arc_hit and local_hit:
            mode = RouteMode.MIXED
            reasons.append(RouteReasonCode.MULTIPLE_SCOPE_SIGNALS)
        elif arc_hit:
            mode = RouteMode.ARC
        elif local_hit:
            mode = RouteMode.LOCAL
        else:
            mode = RouteMode.MIXED
            reasons.append(RouteReasonCode.SAFE_DEFAULT)
        return RouteDecision(
            mode=mode,
            start_levels=_START_LEVELS[mode],
            reason_codes=tuple(dict.fromkeys(reasons)),
            policy_version=policy_version,
            policy_hash=policy_hash,
        )

    if global_hit and full_book_authorized and signal_count == 1:
        return RouteDecision(
            mode=RouteMode.GLOBAL,
            start_levels=_START_LEVELS[RouteMode.GLOBAL],
            reason_codes=tuple(dict.fromkeys(reasons)),
            policy_version=policy_version,
            policy_hash=policy_hash,
        )

    if signal_count >= 2:
        reasons.append(RouteReasonCode.MULTIPLE_SCOPE_SIGNALS)
        if global_hit and full_book_authorized:
            # global + others with authorization → mixed (bounded union)
            mode = RouteMode.MIXED
        elif arc_hit and local_hit:
            mode = RouteMode.MIXED
        elif global_hit and full_book_authorized and not (arc_hit or local_hit):
            mode = RouteMode.GLOBAL
        else:
            mode = RouteMode.MIXED
            reasons.append(RouteReasonCode.AMBIGUOUS_SCOPE)
        return RouteDecision(
            mode=mode,
            start_levels=_START_LEVELS[mode],
            reason_codes=tuple(dict.fromkeys(reasons)),
            policy_version=policy_version,
            policy_hash=policy_hash,
        )

    if arc_hit:
        return RouteDecision(
            mode=RouteMode.ARC,
            start_levels=_START_LEVELS[RouteMode.ARC],
            reason_codes=tuple(dict.fromkeys(reasons)),
            policy_version=policy_version,
            policy_hash=policy_hash,
        )

    if local_hit:
        return RouteDecision(
            mode=RouteMode.LOCAL,
            start_levels=_START_LEVELS[RouteMode.LOCAL],
            reason_codes=tuple(dict.fromkeys(reasons)),
            policy_version=policy_version,
            policy_hash=policy_hash,
        )

    reasons.append(RouteReasonCode.SAFE_DEFAULT)
    return RouteDecision(
        mode=RouteMode.MIXED,
        start_levels=_START_LEVELS[RouteMode.MIXED],
        reason_codes=tuple(dict.fromkeys(reasons)),
        policy_version=policy_version,
        policy_hash=policy_hash,
    )


def decide_route_for_scope(
    question: RetrievalQuestion,
    scope: RetrievalScope,
) -> RouteDecision:
    """Route using scope cutoff authorization and pinned policy hashes."""

    if (
        scope.policy_version != ROUTING_POLICY_VERSION
        or scope.policy_hash != ROUTING_POLICY_HASH
    ):
        raise ValueError("scope policy does not match routing policy")
    return decide_route(
        question,
        full_book_authorized=scope.full_book_authorized,
        policy_version=scope.policy_version,
        policy_hash=scope.policy_hash,
    )


def start_levels_for_mode(mode: RouteMode) -> tuple[StartLevel, ...]:
    return _START_LEVELS[mode]
