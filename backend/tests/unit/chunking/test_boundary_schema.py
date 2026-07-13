"""07-03 boundary decision schema tests."""
from __future__ import annotations
import pytest
from pydantic import ValidationError
from app.services.chunking.adjudicator import validate_boundary_decision
from app.services.chunking.manifests import content_hash
from app.services.chunking.rules import analyze_chapter, RuleEngineConfig
from app.services.chunking.schemas import BoundaryDecision, ContextPreserve

pytestmark = pytest.mark.unit

def _eligible_pair():
    text = "普通一句。" * 10 + "另一句内容。" * 10
    # high auto_accept so medium/high rule scores still enter adjudication path for tests
    cfg = RuleEngineConfig(min_chunk_size=50, max_chunk_size=120, auto_accept=0.99)
    spans, props = analyze_chapter(chapter_id=1, chapter_number=1, content=text, cfg=cfg)
    adj = [p for p in props if p.llm_eligible]
    if not adj:
        adj = [p for p in props if not p.hard_constraint]
    assert adj, "need non-hard proposal for schema tests"
    return spans, adj[0]

def test_valid_decision_ok():
    spans, prop = _eligible_pair()
    raw = {
        "schema_version": "boundary-decision.v1",
        "boundary_id": prop.proposal_id,
        "decision": "merge",
        "reason_codes": ["TARGET_SIZE"],
        "left_span_id": prop.left_span_id,
        "right_span_id": prop.right_span_id,
        "confidence": 0.8,
        "context_preserve": {"keep_left_span_ids": [], "keep_right_span_ids": []},
    }
    d = validate_boundary_decision(raw, prop, {s.span_id: s for s in spans})
    assert d.decision == "merge"

def test_extra_field_rejected():
    spans, prop = _eligible_pair()
    with pytest.raises(ValidationError):
        BoundaryDecision.model_validate({
            "schema_version": "boundary-decision.v1",
            "boundary_id": prop.proposal_id,
            "decision": "split",
            "reason_codes": ["TIME_SHIFT"],
            "left_span_id": prop.left_span_id,
            "right_span_id": prop.right_span_id,
            "confidence": 0.5,
            "content": "forbidden",
        })

def test_wrong_boundary_id_rejected():
    spans, prop = _eligible_pair()
    raw = {
        "schema_version": "boundary-decision.v1",
        "boundary_id": "bp_wrongwrong01",
        "decision": "split",
        "reason_codes": ["TARGET_SIZE"],
        "left_span_id": prop.left_span_id,
        "right_span_id": prop.right_span_id,
        "confidence": 0.5,
    }
    with pytest.raises(ValueError, match="boundary_id"):
        validate_boundary_decision(raw, prop, {s.span_id: s for s in spans})

def test_hard_constraint_cannot_validate():
    text = "字" * 40 + "。" + "词" * 40 + "。"
    spans, props = analyze_chapter(chapter_id=2, chapter_number=1, content=text, cfg=RuleEngineConfig(10, 20))
    hard = [p for p in props if p.hard_constraint and "HARD_MAX_SIZE" in p.reason_codes]
    assert hard
    p = hard[0]
    raw = {
        "schema_version": "boundary-decision.v1",
        "boundary_id": p.proposal_id,
        "decision": "merge",
        "reason_codes": ["HARD_MAX_SIZE"],
        "left_span_id": p.left_span_id,
        "right_span_id": p.right_span_id,
        "confidence": 0.9,
    }
    # hard proposals may reference real spans
    spans_by = {s.span_id: s for s in spans}
    if p.left_span_id in spans_by and p.right_span_id in spans_by:
        with pytest.raises(ValueError, match="hard_constraint"):
            validate_boundary_decision(raw, p, spans_by)
