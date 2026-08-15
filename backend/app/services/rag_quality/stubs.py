"""Stub SUT + answer judge (offline unit/contract) (rag_quality package)."""

from __future__ import annotations

import time
from typing import Any

from app.schemas.eval import EvalCase, ModelLineage, SourceSnapshot

from .metrics import (
    _normalize_tokens,
    claim_supported_by_evidence,
    deterministic_claim_metrics,
)


def default_stub_retrieve(
    case: EvalCase, snapshot: SourceSnapshot, top_k: int = 5
) -> list[dict[str, Any]]:
    """Oracle-aligned retrieval for offline tests: return gold evidence first.

    When gold/equivalent evidence exists, return only those hits (precision=1).
    Otherwise pad with snapshot chunks so no-answer / empty-gold cases still retrieve.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for es in case.equivalent_evidence_sets:
        for ref in es.refs:
            if ref.chunk_content_hash in seen:
                continue
            seen.add(ref.chunk_content_hash)
            results.append(
                {
                    "chunk_content_hash": ref.chunk_content_hash,
                    "quote_text": ref.quote_text,
                    "start_offset": ref.start_offset,
                    "end_offset": ref.end_offset,
                    "score": 1.0,
                }
            )
            if len(results) >= top_k:
                return results
    if results:
        return results
    # No gold evidence — return snapshot chunks as distractors
    for ch in snapshot.chunks:
        if ch.content_hash in seen:
            continue
        results.append(
            {
                "chunk_content_hash": ch.content_hash,
                "quote_text": ch.text,
                "score": 0.1,
            }
        )
        if len(results) >= top_k:
            break
    return results


def default_stub_answer(
    case: EvalCase, retrieved: list[dict[str, Any]]
) -> dict[str, Any]:
    """Oracle-aligned SUT answer for offline tests."""
    t0 = time.perf_counter()
    if case.case_type == "no_answer":
        answer = "insufficient evidence"
    elif case.case_type == "hard_negative":
        answer = "insufficient equivalent evidence"
    else:
        answer = case.reference_answer or (
            retrieved[0].get("quote_text") if retrieved else "unknown"
        )
    latency_ms = (time.perf_counter() - t0) * 1000
    return {
        "answer": answer,
        "parsed_claims": [c.text for c in case.claims],
        "token_usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        },
        "cost_usd": 0.001,
        "latency_ms": latency_ms,
        "status": "ok",
    }


def default_stub_answer_judge(
    case: EvalCase,
    answer: str,
    retrieved: list[dict[str, Any]],
    lineage: ModelLineage,
) -> dict[str, Any]:
    """Deterministic offline answer judge (not a live model)."""
    _ = lineage
    det = deterministic_claim_metrics(case, answer, retrieved)
    faith = float(det["faithfulness_proxy"])
    crit_unsupported = int(det["critical_unsupported_count"])

    # Oracle-aligned offline path: reference match / correct refuse => full credit.
    # Gold claim text may not token-overlap evidence quotes (Chinese paraphrase).
    ans_norm = (answer or "").strip().lower()
    ref_norm = (case.reference_answer or "").strip().lower()
    if case.case_type == "no_answer" and det.get("refused"):
        faith = 1.0
        crit_unsupported = 0
    elif case.case_type == "hard_negative" and (
        "insufficient" in ans_norm or "证据" in (answer or "") or ans_norm == ref_norm
    ):
        faith = 1.0
        crit_unsupported = 0
    elif ref_norm and (
        ans_norm == ref_norm or ref_norm in ans_norm or ans_norm in ref_norm
    ):
        faith = 1.0
        crit_unsupported = 0
    elif retrieved and claim_supported_by_evidence(
        answer,
        [str(item.get("quote_text") or item.get("text") or "") for item in retrieved],
    ):
        faith = max(faith, 0.95)
        crit_unsupported = 0

    # relevance: answer token overlap with question / reference
    q_toks = _normalize_tokens(case.question)
    a_toks = _normalize_tokens(answer)
    if not q_toks:
        relevance = 1.0
    else:
        relevance = len(q_toks & a_toks) / len(q_toks)
        if case.case_type == "no_answer" and det.get("refused"):
            relevance = 1.0
        elif case.reference_answer:
            ref_toks = _normalize_tokens(case.reference_answer)
            if ref_toks:
                relevance = max(relevance, len(ref_toks & a_toks) / len(ref_toks))
    if ref_norm and ans_norm == ref_norm:
        relevance = max(relevance, 0.95)
    relevance = min(1.0, max(0.0, relevance))
    return {
        "faithfulness": faith,
        "relevance": relevance,
        "claim_verdicts": det["claim_verdicts"],
        "critical_unsupported_count": crit_unsupported,
        "critical_ambiguity": 0,
        "reason_codes": ["stub_answer_judge"],
    }
