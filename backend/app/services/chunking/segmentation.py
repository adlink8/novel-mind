"""Deterministic candidate segmentation from boundary proposals (07-02)."""

from __future__ import annotations

from app.services.chunking.manifests import content_hash
from app.services.chunking.rules import RuleEngineConfig, analyze_chapter
from app.services.chunking.schemas import (
    AUTO_ACCEPT_THRESHOLD,
    AtomicSpan,
    BoundaryProposal,
    CandidateSegment,
    CandidateSegmentation,
)
from app.services.rag_fixture import stable_hash


def _segment_id(
    *,
    chapter_id: int,
    index: int,
    span_ids: list[str],
    content_hash_value: str,
) -> str:
    digest = stable_hash(
        {
            "chapter_id": chapter_id,
            "index": index,
            "span_ids": span_ids,
            "content_hash": content_hash_value,
        }
    )
    return f"cs_{digest[:24]}"


def _effective_decision(
    proposal: BoundaryProposal,
    *,
    auto_accept: float = AUTO_ACCEPT_THRESHOLD,
) -> tuple[str, str]:
    """Return (decision, source) for segmentation.

    - hard or confidence >= auto_accept: apply rule_decision (abstain → fallback)
    - else: pending adjudication, use fallback_decision now
    """
    if proposal.hard_constraint:
        d = proposal.rule_decision
        if d == "abstain":
            d = proposal.fallback_decision
        return d, "hard_rule"

    if proposal.confidence >= auto_accept:
        d = proposal.rule_decision
        if d == "abstain":
            d = proposal.fallback_decision
            return d, "rule_fallback"
        return d, "rule_auto"

    # Low / medium confidence: queue for adjudication, apply conservative fallback
    return proposal.fallback_decision, "pending_fallback"


def segment_from_proposals(
    spans: list[AtomicSpan],
    proposals: list[BoundaryProposal],
    *,
    cfg: RuleEngineConfig | None = None,
    source_snapshot_hash: str | None = None,
) -> CandidateSegmentation:
    """Build non-overlapping full-coverage candidate segments for one chapter."""
    cfg = cfg or RuleEngineConfig()
    if not spans:
        empty_checksum = stable_hash({"spans": [], "segments": []})
        return CandidateSegmentation(
            chapter_id=0,
            chapter_number=0,
            source_snapshot_hash=source_snapshot_hash,
            spans=[],
            proposals=proposals,
            segments=[],
            pending_adjudication=[],
            rule_config_hash=cfg.config_hash(),
            segmentation_checksum=empty_checksum,
        )

    chapter_id = spans[0].chapter_id
    chapter_number = spans[0].chapter_number
    span_by_id = {s.span_id: s for s in spans}

    # Index adjacent proposals by left_span_id (ignore chapter-edge synthetics not in spans)
    adj: dict[str, BoundaryProposal] = {}
    pending: list[str] = []
    for p in proposals:
        if p.left_span_id in span_by_id and p.right_span_id in span_by_id:
            adj[p.left_span_id] = p
            if p.llm_eligible:
                pending.append(p.proposal_id)

    segments: list[CandidateSegment] = []
    current: list[AtomicSpan] = []
    decision_notes: list[str] = []

    def flush(notes: list[str]) -> None:
        nonlocal current
        if not current:
            return
        text = "\n".join(s.content for s in current)
        c_hash = content_hash(text)
        ids = [s.span_id for s in current]
        idx = len(segments)
        segments.append(
            CandidateSegment(
                segment_id=_segment_id(
                    chapter_id=chapter_id,
                    index=idx,
                    span_ids=ids,
                    content_hash_value=c_hash,
                ),
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                index=idx,
                span_ids=ids,
                content=text,
                content_hash=c_hash,
                source_start=current[0].source_start,
                source_end=current[-1].source_end,
                char_count=len(text),
                decision_sources=list(notes),
            )
        )
        current = []

    for i, span in enumerate(spans):
        if not current:
            current = [span]
            decision_notes = ["start"]
        else:
            # should not happen if we always extend or flush
            current.append(span)

        # Boundary after this span?
        if i >= len(spans) - 1:
            flush(decision_notes)
            break

        prop = adj.get(span.span_id)
        if prop is None:
            # Missing proposal — conservative merge if within hard max
            nxt = spans[i + 1]
            if sum(s.char_count for s in current) + nxt.char_count <= cfg.max_chunk_size:
                decision_notes.append("implicit_merge")
                continue
            flush(decision_notes + ["implicit_split_hard_max"])
            continue

        decision, source = _effective_decision(prop, auto_accept=cfg.auto_accept)
        decision_notes.append(f"{source}:{decision}")

        if decision == "split":
            flush(decision_notes)
            decision_notes = []
        else:
            # merge: keep accumulating; enforce hard max safety
            nxt = spans[i + 1]
            if sum(s.char_count for s in current) + nxt.char_count > cfg.max_chunk_size:
                flush(decision_notes + ["hard_max_override_split"])
                decision_notes = []

    # Safety: if loop left residual
    if current:
        flush(decision_notes or ["tail"])

    # Validate coverage: all span ids appear once
    seen: list[str] = []
    for seg in segments:
        seen.extend(seg.span_ids)
    if seen != [s.span_id for s in spans]:
        # Repair: force one-span-per-segment if invariant broken
        segments = []
        for idx, s in enumerate(spans):
            c_hash = content_hash(s.content)
            segments.append(
                CandidateSegment(
                    segment_id=_segment_id(
                        chapter_id=chapter_id,
                        index=idx,
                        span_ids=[s.span_id],
                        content_hash_value=c_hash,
                    ),
                    chapter_id=chapter_id,
                    chapter_number=chapter_number,
                    index=idx,
                    span_ids=[s.span_id],
                    content=s.content,
                    content_hash=c_hash,
                    source_start=s.source_start,
                    source_end=s.source_end,
                    char_count=s.char_count,
                    decision_sources=["repair_coverage"],
                )
            )

    # No chapter cross: all same chapter_id
    for seg in segments:
        if seg.chapter_id != chapter_id:
            raise ValueError("segment crossed chapter boundary")

    checksum = stable_hash(
        {
            "chapter_id": chapter_id,
            "span_ids": [s.span_id for s in spans],
            "segment_ids": [s.segment_id for s in segments],
            "pending": pending,
            "cfg": cfg.config_hash(),
        }
    )
    return CandidateSegmentation(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_snapshot_hash=source_snapshot_hash,
        spans=spans,
        proposals=proposals,
        segments=segments,
        pending_adjudication=pending,
        rule_config_hash=cfg.config_hash(),
        segmentation_checksum=checksum,
    )


def segment_chapter(
    *,
    chapter_id: int,
    chapter_number: int,
    content: str,
    cfg: RuleEngineConfig | None = None,
    source_snapshot_hash: str | None = None,
) -> CandidateSegmentation:
    """Full pipeline: scan → propose → segment (pure, no I/O)."""
    cfg = cfg or RuleEngineConfig()
    spans, proposals = analyze_chapter(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        content=content,
        cfg=cfg,
        source_snapshot_hash=source_snapshot_hash,
    )
    return segment_from_proposals(
        spans,
        proposals,
        cfg=cfg,
        source_snapshot_hash=source_snapshot_hash,
    )
