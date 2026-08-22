"""Single-case / multi-case run and aggregation (rag_quality package)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.schemas.eval import ChunkerLineage, EvalCase, ModelLineage, SourceSnapshot
from app.services.rag_fixture import schema_contract_hash, stable_hash

from .bootstrap import bootstrap_lower_bound, case_repeat_consistency
from .lineage import build_stage_cache_key
from .metrics import (
    context_precision_at_k,
    context_recall_at_k,
    deterministic_claim_metrics,
)
from .policy import ANSWER_JUDGE_PROMPT_VERSION, answer_judge_prompt_hash
from .stubs import default_stub_answer, default_stub_answer_judge, default_stub_retrieve
from .types import AnswerFn, AnswerJudgeFn, DependencyOutage, RetrieveFn


@dataclass
class CaseRunArtifact:
    case_id: str
    repetition: int
    retrieved: list[dict[str, Any]]
    answer: str
    deterministic_metrics: dict[str, Any]
    judge_scores: dict[str, Any]
    token_usage: dict[str, Any]
    cost_usd: float
    latency_ms: float
    status: str
    quality_comparable: bool
    call_id: str  # idempotency key for stage


def run_case_once(
    case: EvalCase,
    snapshot: SourceSnapshot,
    *,
    repetition: int,
    top_k: int = 5,
    retrieve_fn: RetrieveFn | None = None,
    answer_fn: AnswerFn | None = None,
    judge_fn: AnswerJudgeFn | None = None,
    judge_lineage: ModelLineage | None = None,
    stage_cache: dict[str, Any] | None = None,
    run_input_hash: str | None = None,
    chunker_lineage: ChunkerLineage | dict[str, Any] | None = None,
) -> CaseRunArtifact:
    """Execute retrieve -> answer -> score for one case/repetition (idempotent via cache)."""
    retrieve_fn = retrieve_fn or default_stub_retrieve
    answer_fn = answer_fn or default_stub_answer
    judge_fn = judge_fn or default_stub_answer_judge

    call_id = build_stage_cache_key(
        run_input_hash=run_input_hash,
        case_id=case.case_id,
        fixture_hash=case.fixture_hash,
        repetition=repetition,
        top_k=top_k,
        chunker_lineage=chunker_lineage,
    )

    if stage_cache is not None and call_id in stage_cache:
        cached = stage_cache[call_id]
        return CaseRunArtifact(**cached)

    try:
        retrieved = retrieve_fn(case, snapshot, top_k)
        ans = answer_fn(case, retrieved)
        answer_text = str(ans.get("answer") or "")
        j_lineage = judge_lineage or ModelLineage(
            provider="offline",
            model_family="stub",
            model_id="stub-judge",
            weights_revision="stub-rev",
            prompt_hash=answer_judge_prompt_hash() or ("0" * 64),
            prompt_version=ANSWER_JUDGE_PROMPT_VERSION,
            schema_hash=schema_contract_hash(),
            started_at=datetime.now(timezone.utc),
        )
        # pad prompt_hash if empty
        if len(j_lineage.prompt_hash) != 64:
            j_lineage = j_lineage.model_copy(
                update={"prompt_hash": stable_hash(j_lineage.prompt_hash)}
            )

        judge_scores = judge_fn(case, answer_text, retrieved, j_lineage)
        det = {
            "context_precision": context_precision_at_k(case, retrieved, top_k),
            "context_recall_at_5": context_recall_at_k(case, retrieved, top_k),
            **deterministic_claim_metrics(case, answer_text, retrieved),
        }
        # Prefer judge faithfulness; enforce critical from deterministic recount
        if "critical_unsupported_count" in judge_scores:
            # Arbiter re-checks deterministic critical rate
            pass

        artifact = CaseRunArtifact(
            case_id=case.case_id,
            repetition=repetition,
            retrieved=retrieved,
            answer=answer_text,
            deterministic_metrics=det,
            judge_scores=judge_scores,
            token_usage=ans.get("token_usage") or {},
            cost_usd=float(ans.get("cost_usd") or 0.0),
            latency_ms=float(ans.get("latency_ms") or 0.0),
            status="scored",
            quality_comparable=False,  # set by arbiter
            call_id=call_id,
        )
        if stage_cache is not None:
            stage_cache[call_id] = {
                "case_id": artifact.case_id,
                "repetition": artifact.repetition,
                "retrieved": artifact.retrieved,
                "answer": artifact.answer,
                "deterministic_metrics": artifact.deterministic_metrics,
                "judge_scores": artifact.judge_scores,
                "token_usage": artifact.token_usage,
                "cost_usd": artifact.cost_usd,
                "latency_ms": artifact.latency_ms,
                "status": artifact.status,
                "quality_comparable": artifact.quality_comparable,
                "call_id": artifact.call_id,
            }
        return artifact
    except DependencyOutage:
        return CaseRunArtifact(
            case_id=case.case_id,
            repetition=repetition,
            retrieved=[],
            answer="",
            deterministic_metrics={},
            judge_scores={},
            token_usage={},
            cost_usd=0.0,
            latency_ms=0.0,
            status="blocked_dependency",
            quality_comparable=False,
            call_id=call_id,
        )


def aggregate_run_metrics(
    artifacts: list[CaseRunArtifact],
    *,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate per-run metrics from case/repetition artifacts."""
    run_cfg = policy.get("run") or {}
    n_boot = int(run_cfg.get("bootstrap_samples", 1000))
    alpha = float(run_cfg.get("bootstrap_alpha", 0.05))
    seed = int(run_cfg.get("bootstrap_seed", 42))

    faith_scores: list[float] = []
    relevance_scores: list[float] = []
    ctx_prec: list[float] = []
    ctx_rec: list[float] = []
    crit_rates: list[float] = []
    costs: list[float] = []
    latencies: list[float] = []
    tokens: list[int] = []
    per_case_verdicts: dict[str, list[str]] = {}

    for a in artifacts:
        if a.status == "blocked_dependency":
            continue
        j = a.judge_scores or {}
        d = a.deterministic_metrics or {}
        faith = float(j.get("faithfulness", d.get("faithfulness_proxy", 0.0)))
        # Prefer judge critical count when present; else deterministic rate
        if "critical_unsupported_count" in j:
            crit_count = int(j.get("critical_unsupported_count") or 0)
            crit_rate = 1.0 if crit_count > 0 else 0.0
        else:
            crit_rate = float(d.get("critical_unsupported_claim_rate", 0.0))
        # Fail closed: critical unsupported forces faithfulness contribution to 0 for gate
        if crit_rate > 0:
            faith = min(faith, 0.0)
        faith_scores.append(faith)
        relevance_scores.append(float(j.get("relevance", 0.0)))
        ctx_prec.append(float(d.get("context_precision", 0.0)))
        ctx_rec.append(float(d.get("context_recall_at_5", 0.0)))
        crit_rates.append(crit_rate)
        costs.append(a.cost_usd)
        latencies.append(a.latency_ms)
        tokens.append(int((a.token_usage or {}).get("total_tokens") or 0))

        # Per-repeat case-level pass/fail verdict for consistency
        case_pass = (
            crit_rate == 0.0 and faith >= 0.90 and float(j.get("relevance", 0.0)) >= 0.5
        )
        per_case_verdicts.setdefault(a.case_id, []).append(
            "pass" if case_pass else "fail"
        )

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def _p95(xs: list[float]) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        idx = min(len(s) - 1, max(0, int(math.ceil(0.95 * len(s)) - 1)))
        return float(s[idx])

    consistency = case_repeat_consistency(list(per_case_verdicts.values()))
    faith_lb = bootstrap_lower_bound(
        faith_scores, n_boot=n_boot, alpha=alpha, seed=seed
    )
    return {
        "answer_faithfulness_mean": _mean(faith_scores),
        "answer_faithfulness_95lb": faith_lb,
        "answer_relevance_mean": _mean(relevance_scores),
        "context_precision_mean": _mean(ctx_prec),
        "context_recall_at_5_mean": _mean(ctx_rec),
        "critical_unsupported_claim_rate": max(crit_rates) if crit_rates else 0.0,
        "verdict_consistency": consistency,
        "cost_usd_total": sum(costs),
        "cost_usd_mean": _mean(costs),
        "latency_ms_p95": _p95(latencies),
        "tokens_total": sum(tokens),
        "tokens_p95": _p95([float(t) for t in tokens]),
        "n_artifacts": len(artifacts),
        "n_scored": len(faith_scores),
        "per_case_verdicts": per_case_verdicts,
    }
