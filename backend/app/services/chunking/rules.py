"""Atomic span scanner and versioned boundary confidence engine (07-02 / REQ-CHUNK-02)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.services.chunking.baseline import build_offset_map
from app.services.chunking.manifests import content_hash
from app.services.chunking.schemas import (
    ADJUDICATE_THRESHOLD,
    AUTO_ACCEPT_THRESHOLD,
    RULE_CONFIDENCE_VERSION,
    AtomicSpan,
    BoundaryProposal,
    ChunkerConfig,
    ReasonCode,
    RuleDecision,
)
from app.services.chunking_service import DESCRIPTION_KEYWORDS, SCENE_MARKERS
from app.services.rag_fixture import stable_hash

RULES_VERSION = "boundary-rules.v1"

# Split SCENE_MARKERS into time vs location heuristically
_TIME_MARKERS = {
    "翌日", "次日", "翌晨", "清晨", "黄昏", "傍晚", "深夜", "午夜",
    "数日后", "几日后", "半月后", "一月后", "半年后", "一年后",
    "时光飞逝", "光阴似箭", "岁月如梭",
}
_LOCATION_MARKERS = {
    "来到", "走进", "走出", "进入", "离开", "抵达", "到达",
    "回到", "返回", "前往", "赶往", "奔赴",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；…])")
_QUOTE_CHARS = set('“”"「」')
_SPEAKER_PATTERNS = [
    re.compile(r"^[「\"“].*[」\"”]$"),
    re.compile(r".*(说|道|问|答|喊|叫)[道：:]"),
]
_POV_MARKERS = ("我", "他", "她", "他们", "她们", "我们")
_COREFERENCE = ("他", "她", "它", "他们", "这", "那", "此")


@dataclass(frozen=True)
class RuleEngineConfig:
    min_chunk_size: int = 300
    max_chunk_size: int = 500
    auto_accept: float = AUTO_ACCEPT_THRESHOLD
    adjudicate: float = ADJUDICATE_THRESHOLD

    def as_chunker_config(self) -> ChunkerConfig:
        return ChunkerConfig(
            min_chunk_size=self.min_chunk_size,
            max_chunk_size=self.max_chunk_size,
        )

    def config_hash(self) -> str:
        return stable_hash(
            {
                "rules_version": RULES_VERSION,
                "confidence_version": RULE_CONFIDENCE_VERSION,
                "min_chunk_size": self.min_chunk_size,
                "max_chunk_size": self.max_chunk_size,
                "auto_accept": self.auto_accept,
                "adjudicate": self.adjudicate,
            }
        )


def _span_id(
    *,
    chapter_id: int,
    index: int,
    content_hash_value: str,
    source_start: int,
    source_end: int,
) -> str:
    digest = stable_hash(
        {
            "chapter_id": chapter_id,
            "index": index,
            "content_hash": content_hash_value,
            "source_start": source_start,
            "source_end": source_end,
        }
    )
    return f"as_{digest[:24]}"


def _proposal_id(
    *,
    left_id: str,
    right_id: str,
    decision: str,
    reasons: Iterable[str],
    confidence: float,
) -> str:
    digest = stable_hash(
        {
            "left": left_id,
            "right": right_id,
            "decision": decision,
            "reasons": sorted(reasons),
            "confidence": round(confidence, 6),
            "version": RULES_VERSION,
        }
    )
    return f"bp_{digest[:24]}"


def scan_atomic_spans(
    *,
    chapter_id: int,
    chapter_number: int,
    content: str,
) -> list[AtomicSpan]:
    """Scan chapter into sentence-level atomic spans with source offsets."""
    omap = build_offset_map(content or "")
    normalized = omap.normalized
    if not normalized.strip():
        return []

    # Split into sentence-like units while preserving offsets in normalized text
    pieces: list[tuple[str, int, int]] = []
    # Prefer sentence boundaries; fallback to whole paragraph lines
    parts = _SENTENCE_SPLIT.split(normalized)
    buf = ""
    buf_start = 0
    pos = 0
    for part in parts:
        if not part:
            continue
        if not buf:
            buf_start = pos
        buf += part
        pos += len(part)
        if part and part[-1] in "。！？；…":
            text = buf.strip()
            if text:
                # locate text in [buf_start, pos) with strip
                region = normalized[buf_start:pos]
                lead = len(region) - len(region.lstrip())
                trail = len(region) - len(region.rstrip())
                n_start = buf_start + lead
                n_end = pos - trail
                pieces.append((text, n_start, n_end))
            buf = ""
            buf_start = pos
    if buf.strip():
        region = normalized[buf_start:pos] if pos > buf_start else buf
        # recompute from end of last piece
        n_start = buf_start
        while n_start < len(normalized) and normalized[n_start] in " \t\n":
            n_start += 1
        n_end = n_start + len(buf.strip())
        # clamp
        n_end = min(n_end, len(normalized))
        text = normalized[n_start:n_end].strip() or buf.strip()
        # refine by searching forward from n_start
        idx = normalized.find(text, n_start)
        if idx >= 0:
            n_start, n_end = idx, idx + len(text)
        pieces.append((text, n_start, n_end))

    # If sentence split produced nothing useful, fall back to non-empty lines
    if not pieces:
        line_start = 0
        for i, ch in enumerate(normalized + "\n"):
            if ch != "\n":
                continue
            line = normalized[line_start:i]
            stripped = line.strip()
            if stripped:
                lead = len(line) - len(line.lstrip())
                n_start = line_start + lead
                n_end = n_start + len(stripped)
                pieces.append((stripped, n_start, n_end))
            line_start = i + 1

    spans: list[AtomicSpan] = []
    for idx, (text, n_start, n_end) in enumerate(pieces):
        if n_end < n_start:
            n_end = n_start
        s_start, s_end = omap.source_span(n_start, min(n_end, len(normalized)))
        c_hash = content_hash(text)
        sid = _span_id(
            chapter_id=chapter_id,
            index=idx,
            content_hash_value=c_hash,
            source_start=s_start,
            source_end=s_end,
        )
        spans.append(
            AtomicSpan(
                span_id=sid,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                index=idx,
                content=text,
                content_hash=c_hash,
                source_start=s_start,
                source_end=s_end,
                normalized_start=n_start,
                normalized_end=n_end,
                char_count=len(text),
            )
        )
    return spans


def _unbalanced_quotes(text: str) -> bool:
    # Count Chinese / English / corner quotes
    # Simple parity on each open/close class
    opens = text.count("“") + text.count("「") + text.count('"')
    closes = text.count("”") + text.count("」")
    # For ASCII ", odd count means open
    ascii_q = text.count('"')
    if ascii_q % 2 == 1:
        return True
    return opens != closes and (opens + closes) > 0


def _looks_dialogue(text: str) -> bool:
    if any(c in text for c in _QUOTE_CHARS):
        return True
    return any(p.search(text) for p in _SPEAKER_PATTERNS)


def _has_marker(text: str, markers: set[str]) -> bool:
    return any(m in text for m in markers)


def _score_boundary(
    left: AtomicSpan,
    right: AtomicSpan,
    *,
    combined_size: int,
    cfg: RuleEngineConfig,
) -> tuple[RuleDecision, float, list[ReasonCode], bool]:
    """Return decision, confidence score, reasons, hard_constraint flag."""
    reasons: list[ReasonCode] = []
    hard = False
    # Default lean merge for continuous prose
    split_score = 0.35
    merge_score = 0.55

    # Hard max: must split if either side alone or combined exceeds max
    if left.char_count >= cfg.max_chunk_size or right.char_count >= cfg.max_chunk_size:
        reasons.append("HARD_MAX_SIZE")
        hard = True
        return "split", 1.0, reasons, hard
    if combined_size > cfg.max_chunk_size:
        reasons.append("HARD_MAX_SIZE")
        hard = True
        return "split", 1.0, reasons, hard

    if combined_size < cfg.min_chunk_size:
        reasons.append("UNDER_MIN_SIZE")
        merge_score += 0.25

    # Target size band
    mid = (cfg.min_chunk_size + cfg.max_chunk_size) / 2
    if combined_size <= cfg.max_chunk_size and abs(combined_size - mid) < mid * 0.25:
        reasons.append("TARGET_SIZE")
        # comfortable size — weak preference
        merge_score += 0.05
    if combined_size > mid and combined_size <= cfg.max_chunk_size:
        reasons.append("TARGET_SIZE")
        split_score += 0.1

    # Structural: blank-line style already lost; use double punctuation / scene
    if right.content.startswith(("※", "——", "—", "【", "第")):
        reasons.append("STRUCTURAL_BREAK")
        split_score += 0.3

    if _has_marker(right.content, _TIME_MARKERS) or _has_marker(left.content[-20:], _TIME_MARKERS):
        reasons.append("TIME_SHIFT")
        split_score += 0.25
    if _has_marker(right.content, _LOCATION_MARKERS):
        reasons.append("LOCATION_SHIFT")
        split_score += 0.2

    # Speaker / dialogue shift
    left_dlg = _looks_dialogue(left.content)
    right_dlg = _looks_dialogue(right.content)
    if left_dlg != right_dlg:
        reasons.append("SPEAKER_SHIFT")
        split_score += 0.15
    elif left_dlg and right_dlg:
        # continuous dialogue — prefer merge
        merge_score += 0.1

    # POV shift heuristic: first char pronoun change
    left_pov = next((p for p in _POV_MARKERS if p in left.content[:12]), None)
    right_pov = next((p for p in _POV_MARKERS if p in right.content[:12]), None)
    if left_pov and right_pov and left_pov != right_pov:
        reasons.append("POV_SHIFT")
        split_score += 0.15

    # Open quote / coreference risk → abstain-leaning low confidence
    if _unbalanced_quotes(left.content) or _unbalanced_quotes(right.content):
        reasons.append("OPEN_QUOTE")
        split_score -= 0.1
        merge_score -= 0.05
    if any(right.content.startswith(c) for c in _COREFERENCE):
        reasons.append("COREFERENCE_RISK")
        merge_score += 0.12  # keep with previous

    # Description density as weak structural signal
    desc_hits = sum(1 for kw in DESCRIPTION_KEYWORDS if kw in right.content)
    if desc_hits >= 3 and not left_dlg:
        reasons.append("STRUCTURAL_BREAK")
        split_score += 0.08

    # Scene markers from legacy list
    for m in SCENE_MARKERS:
        if m in right.content[:40]:
            if m in _TIME_MARKERS and "TIME_SHIFT" not in reasons:
                reasons.append("TIME_SHIFT")
            elif m in _LOCATION_MARKERS and "LOCATION_SHIFT" not in reasons:
                reasons.append("LOCATION_SHIFT")
            else:
                if "STRUCTURAL_BREAK" not in reasons:
                    reasons.append("STRUCTURAL_BREAK")
            split_score += 0.12
            break

    if not reasons:
        reasons.append("TARGET_SIZE")

    # Decide
    if "OPEN_QUOTE" in reasons and abs(split_score - merge_score) < 0.15:
        decision: RuleDecision = "abstain"
        confidence = max(0.2, min(0.55, (split_score + merge_score) / 2))
    elif split_score >= merge_score + 0.08:
        decision = "split"
        confidence = min(0.98, 0.5 + (split_score - merge_score))
    elif merge_score >= split_score + 0.08:
        decision = "merge"
        confidence = min(0.98, 0.5 + (merge_score - split_score))
    else:
        decision = "abstain"
        confidence = max(0.25, min(0.65, (split_score + merge_score) / 2))

    confidence = round(max(0.0, min(1.0, confidence)), 4)
    return decision, confidence, reasons, hard


def propose_boundaries(
    spans: list[AtomicSpan],
    *,
    cfg: RuleEngineConfig | None = None,
    source_snapshot_hash: str | None = None,
    include_chapter_edge: bool = True,
) -> list[BoundaryProposal]:
    """Emit one proposal per adjacent span pair (+ optional synthetic chapter edges)."""
    cfg = cfg or RuleEngineConfig()
    cfg_hash = cfg.config_hash()
    proposals: list[BoundaryProposal] = []

    if include_chapter_edge and spans:
        # Leading chapter edge (before first span) — synthetic left
        first = spans[0]
        edge_left_hash = content_hash("")
        edge_id_left = "as_chapter_start_" + first.span_id[3:11]
        reasons: list[ReasonCode] = ["CHAPTER_EDGE"]
        pid = _proposal_id(
            left_id=edge_id_left,
            right_id=first.span_id,
            decision="split",
            reasons=reasons,
            confidence=1.0,
        )
        proposals.append(
            BoundaryProposal(
                proposal_id=pid,
                chapter_id=first.chapter_id,
                left_span_id=edge_id_left,
                right_span_id=first.span_id,
                left_content_hash=edge_left_hash,
                right_content_hash=first.content_hash,
                rule_decision="split",
                confidence=1.0,
                reason_codes=reasons,
                hard_constraint=True,
                llm_eligible=False,
                fallback_decision="split",
                input_hash=stable_hash(
                    {"left": edge_id_left, "right": first.span_id, "edge": "start"}
                ),
                rule_config_hash=cfg_hash,
                source_snapshot_hash=source_snapshot_hash,
            )
        )

    for i in range(len(spans) - 1):
        left, right = spans[i], spans[i + 1]
        combined = left.char_count + right.char_count + 1
        decision, confidence, reasons, hard = _score_boundary(
            left, right, combined_size=combined, cfg=cfg
        )
        # Fallback is always a conservative executable decision
        if decision == "abstain":
            fallback: RuleDecision = (
                "merge" if combined <= cfg.max_chunk_size else "split"
            )
            if combined < cfg.min_chunk_size:
                fallback = "merge" if combined <= cfg.max_chunk_size else "split"
        else:
            fallback = decision

        llm_eligible = (not hard) and (confidence < cfg.auto_accept)
        # Never allow hard into LLM path
        if hard:
            llm_eligible = False
            decision = "split" if "HARD_MAX_SIZE" in reasons else decision
            confidence = 1.0
            fallback = decision

        pid = _proposal_id(
            left_id=left.span_id,
            right_id=right.span_id,
            decision=decision,
            reasons=reasons,
            confidence=confidence,
        )
        proposals.append(
            BoundaryProposal(
                proposal_id=pid,
                chapter_id=left.chapter_id,
                left_span_id=left.span_id,
                right_span_id=right.span_id,
                left_content_hash=left.content_hash,
                right_content_hash=right.content_hash,
                rule_decision=decision,
                confidence=confidence,
                reason_codes=reasons,
                hard_constraint=hard,
                llm_eligible=llm_eligible,
                fallback_decision=fallback,
                input_hash=stable_hash(
                    {
                        "left": left.span_id,
                        "right": right.span_id,
                        "lh": left.content_hash,
                        "rh": right.content_hash,
                    }
                ),
                rule_config_hash=cfg_hash,
                source_snapshot_hash=source_snapshot_hash,
            )
        )

    if include_chapter_edge and spans:
        last = spans[-1]
        edge_right_hash = content_hash("")
        edge_id_right = "as_chapter_end_" + last.span_id[3:11]
        reasons = ["CHAPTER_EDGE"]
        pid = _proposal_id(
            left_id=last.span_id,
            right_id=edge_id_right,
            decision="split",
            reasons=reasons,
            confidence=1.0,
        )
        proposals.append(
            BoundaryProposal(
                proposal_id=pid,
                chapter_id=last.chapter_id,
                left_span_id=last.span_id,
                right_span_id=edge_id_right,
                left_content_hash=last.content_hash,
                right_content_hash=edge_right_hash,
                rule_decision="split",
                confidence=1.0,
                reason_codes=reasons,
                hard_constraint=True,
                llm_eligible=False,
                fallback_decision="split",
                input_hash=stable_hash(
                    {"left": last.span_id, "right": edge_id_right, "edge": "end"}
                ),
                rule_config_hash=cfg_hash,
                source_snapshot_hash=source_snapshot_hash,
            )
        )

    return proposals


def analyze_chapter(
    *,
    chapter_id: int,
    chapter_number: int,
    content: str,
    cfg: RuleEngineConfig | None = None,
    source_snapshot_hash: str | None = None,
) -> tuple[list[AtomicSpan], list[BoundaryProposal]]:
    cfg = cfg or RuleEngineConfig()
    spans = scan_atomic_spans(
        chapter_id=chapter_id, chapter_number=chapter_number, content=content
    )
    proposals = propose_boundaries(
        spans, cfg=cfg, source_snapshot_hash=source_snapshot_hash
    )
    return spans, proposals
