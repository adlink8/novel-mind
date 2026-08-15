"""Deterministic retrieval metrics (content-hash truth) (rag_quality package)."""

from __future__ import annotations

import re
from typing import Any

from app.schemas.eval import EvalCase


def _gold_content_hashes(case: EvalCase) -> set[str]:
    hashes: set[str] = set()
    for es in case.equivalent_evidence_sets:
        for ref in es.refs:
            hashes.add(ref.chunk_content_hash)
    return hashes


def _retrieved_hashes(retrieved: list[dict[str, Any]], top_k: int = 5) -> list[str]:
    out: list[str] = []
    for item in retrieved[:top_k]:
        h = item.get("chunk_content_hash") or item.get("content_hash")
        if h:
            out.append(str(h))
    return out


def context_precision_at_k(
    case: EvalCase, retrieved: list[dict[str, Any]], top_k: int = 5
) -> float:
    """Fraction of top-k retrieved chunks that match any gold/equivalent evidence set."""
    gold = _gold_content_hashes(case)
    recalled = _retrieved_hashes(retrieved, top_k)
    if not recalled:
        return 0.0 if gold else 1.0
    hits = sum(1 for h in recalled if h in gold)
    return hits / len(recalled)


def context_recall_at_k(
    case: EvalCase, retrieved: list[dict[str, Any]], top_k: int = 5
) -> float:
    """Fraction of gold equivalent-evidence sets covered by top-k retrieval.

    A set is covered if any of its refs' chunk_content_hash appears in retrieval.
    For no_answer cases with empty gold sets, recall is 1.0 (nothing to recall).
    """
    if case.case_type == "no_answer" or not case.equivalent_evidence_sets:
        return 1.0 if case.case_type == "no_answer" else 0.0

    recalled = set(_retrieved_hashes(retrieved, top_k))
    if not recalled:
        return 0.0
    covered = 0
    for es in case.equivalent_evidence_sets:
        set_hashes = {r.chunk_content_hash for r in es.refs}
        if set_hashes & recalled:
            covered += 1
    return covered / len(case.equivalent_evidence_sets)


def _normalize_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()) if t}


def claim_supported_by_evidence(claim_text: str, evidence_texts: list[str]) -> bool:
    """Deterministic support: claim tokens substantially overlap some evidence quote."""
    claim_toks = _normalize_tokens(claim_text)
    if not claim_toks:
        return False
    joined = " ".join(evidence_texts).lower()
    # Substring containment (Chinese-friendly) or token Jaccard-ish
    if claim_text.strip() and claim_text.strip().lower() in joined:
        return True
    for et in evidence_texts:
        et_toks = _normalize_tokens(et)
        if not et_toks:
            continue
        overlap = len(claim_toks & et_toks) / len(claim_toks)
        if overlap >= 0.5:
            return True
    return False


def deterministic_claim_metrics(
    case: EvalCase,
    answer: str,
    retrieved: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute critical unsupported rate and simple claim-level faithfulness."""
    evidence_texts: list[str] = []
    for item in retrieved:
        for key in ("quote_text", "text", "content"):
            if item.get(key):
                evidence_texts.append(str(item[key]))
                break

    if case.case_type == "no_answer":
        # Fabricated claims if answer looks assertive without refuse markers
        refuse_markers = (
            "cannot",
            "insufficient",
            "unknown",
            "无法",
            "证据不足",
            "不足以",
            "no evidence",
            "refuse",
        )
        lowered = (answer or "").lower()
        refused = (
            any(m in lowered for m in refuse_markers) or not (answer or "").strip()
        )
        if refused:
            return {
                "faithfulness_proxy": 1.0,
                "critical_unsupported_claim_rate": 0.0,
                "critical_unsupported_count": 0,
                "claim_verdicts": [],
                "refused": True,
            }
        return {
            "faithfulness_proxy": 0.0,
            "critical_unsupported_claim_rate": 1.0,
            "critical_unsupported_count": 1,
            "claim_verdicts": [
                {
                    "claim_text": answer[:200],
                    "supported": False,
                    "critical": True,
                }
            ],
            "refused": False,
        }

    claims = case.claims or []
    # If no structured claims, treat whole answer as one non-critical claim proxy
    if not claims and answer:
        supported = claim_supported_by_evidence(answer, evidence_texts)
        return {
            "faithfulness_proxy": 1.0 if supported else 0.0,
            "critical_unsupported_claim_rate": 0.0,
            "critical_unsupported_count": 0,
            "claim_verdicts": [
                {"claim_text": answer[:200], "supported": supported, "critical": False}
            ],
            "refused": False,
        }

    verdicts = []
    critical_total = 0
    critical_unsupported = 0
    supported_count = 0
    for c in claims:
        supported = claim_supported_by_evidence(c.text, evidence_texts)
        if c.critical:
            critical_total += 1
            if not supported:
                critical_unsupported += 1
        if supported:
            supported_count += 1
        verdicts.append(
            {
                "claim_id": c.claim_id,
                "claim_text": c.text,
                "supported": supported,
                "critical": c.critical,
            }
        )

    n = len(claims) or 1
    rate = (critical_unsupported / critical_total) if critical_total else 0.0
    return {
        "faithfulness_proxy": supported_count / n,
        "critical_unsupported_claim_rate": rate,
        "critical_unsupported_count": critical_unsupported,
        "claim_verdicts": verdicts,
        "refused": False,
    }
