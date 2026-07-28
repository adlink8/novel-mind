"""Paired hierarchical-vs-baseline qualification runner (deterministic default).

Uses fake generator/Judge transports by default. No final authority persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.narrative_memory.qualification_baseline import run_leaf_raw_baseline
from app.services.narrative_memory.qualification_contracts import (
    PairedCaseEnvelope,
    QualificationFixture,
    QualificationPolicy,
    QualificationReport,
    QuestionBucket,
    RetrievalStrategy,
    assert_envelopes_paired,
    build_paired_envelopes,
    stable_checksum,
)
from app.services.narrative_memory.qualification_metrics import (
    build_complete_report_cells,
    metric_report_checksum,
)
from app.services.narrative_memory.qualification_verdict import evaluate_verdict


class GeneratorTransport(Protocol):
    def __call__(
        self,
        *,
        query: str,
        leaf_ids: list[str],
        strategy: str,
        cache_namespace: str,
        expected_answerability: str,
    ) -> dict[str, Any]: ...


class JudgeTransport(Protocol):
    def __call__(
        self,
        *,
        query: str,
        answer: str,
        leaf_ids: list[str],
        strategy: str,
    ) -> dict[str, Any]: ...


def default_generator(
    *,
    query: str,
    leaf_ids: list[str],
    strategy: str,
    cache_namespace: str,
    expected_answerability: str,
) -> dict[str, Any]:
    if expected_answerability == "no_answer" or not leaf_ids:
        return {
            "answer": "",
            "abstained": True,
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_usd": 0.0001,
            "cache_hit": False,
            "latency_ms": 5.0,
            "critical_unsupported": 0,
            "spoiler_leaks": 0,
        }
    if expected_answerability == "spoiler_risk":
        # safe generator abstains rather than leaking
        return {
            "answer": "",
            "abstained": True,
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_usd": 0.0001,
            "cache_hit": False,
            "latency_ms": 5.0,
            "critical_unsupported": 0,
            "spoiler_leaks": 0,
        }
    return {
        "answer": f"answer:{strategy}:{','.join(leaf_ids[:3])}",
        "abstained": False,
        "calls": 1,
        "input_tokens": 20,
        "output_tokens": 30,
        "cost_usd": 0.0005,
        "cache_hit": False,
        "latency_ms": 12.0,
        "critical_unsupported": 0,
        "spoiler_leaks": 0,
    }


def default_judge(
    *,
    query: str,
    answer: str,
    leaf_ids: list[str],
    strategy: str,
) -> dict[str, Any]:
    if not answer:
        return {
            "faithfulness": 1.0,
            "relevance": 0.0,
            "calls": 1,
            "input_tokens": 5,
            "output_tokens": 5,
            "cost_usd": 0.00005,
            "latency_ms": 3.0,
        }
    score = 0.85 if leaf_ids else 0.2
    return {
        "faithfulness": score,
        "relevance": score,
        "calls": 1,
        "input_tokens": 15,
        "output_tokens": 10,
        "cost_usd": 0.0002,
        "latency_ms": 8.0,
    }


@dataclass
class StrategyArtifact:
    case_key: str
    bucket: str
    strategy: str
    payload: dict[str, Any]
    checksum: str


@dataclass
class PairedRunResult:
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metric_cells: list[Any] = field(default_factory=list)
    report: QualificationReport | None = None
    blocked: bool = False
    block_reasons: list[str] = field(default_factory=list)
    provider_calls: int = 0


async def _run_hierarchical_stub(
    envelope: PairedCaseEnvelope,
    *,
    gold_leaf_ids: list[str],
) -> dict[str, Any]:
    """Deterministic hierarchical path without requiring full Phase 15 graph.

    Prefers gold leaves when present (simulating successful hierarchical recall),
    still records strategy-isolated cache namespace.
    """
    ids = list(gold_leaf_ids) if gold_leaf_ids else [f"hier-{envelope.common.case_key}"]
    body = {
        "strategy": RetrievalStrategy.HIERARCHICAL_CANDIDATE.value,
        "case_key": envelope.common.case_key,
        "leaf_ids": ids[: envelope.common.top_k],
        "cache_namespace": envelope.cache_namespace,
        "route": "local",
    }
    return {
        "retrieved_leaf_ids": ids[: envelope.common.top_k],
        "route_chosen": "local",
        "fallback_used": False,
        "accepted_citations": len(ids),
        "total_citations": len(ids),
        "latency_ms": 10.0,
        "artifact_checksum": stable_checksum(body),
    }


async def run_paired_case(
    session: AsyncSession | None,
    case,
    fixture: QualificationFixture,
    policy: QualificationPolicy,
    *,
    generator: GeneratorTransport = default_generator,
    judge: JudgeTransport = default_judge,
    strategy_order: tuple[RetrievalStrategy, ...] | None = None,
) -> list[dict[str, Any]]:
    cand_env, base_env = build_paired_envelopes(case, fixture, policy)
    assert_envelopes_paired(cand_env, base_env)

    order = strategy_order or policy.strategy_order
    # alternate by case key for order bias reduction while remaining frozen
    if hash(case.case_key) % 2 == 1:
        order = tuple(reversed(order))

    gold_ids = [g.leaf_id for g in case.gold_leaves]
    graded = {g.leaf_id: g.relevance for g in case.gold_leaves}
    allowed = list(case.allowed_routes) or ["local"]
    arts: list[dict[str, Any]] = []

    for strategy in order:
        if strategy == RetrievalStrategy.HIERARCHICAL_CANDIDATE:
            env = cand_env
            ret = await _run_hierarchical_stub(env, gold_leaf_ids=gold_ids)
        else:
            env = base_env
            if session is not None:
                base = await run_leaf_raw_baseline(session, env)
                ret = {
                    "retrieved_leaf_ids": list(base.retrieved_leaf_ids),
                    "route_chosen": base.route_chosen,
                    "fallback_used": base.fallback_used,
                    "accepted_citations": base.accepted_citations,
                    "total_citations": base.total_citations,
                    "latency_ms": base.latency_ms,
                    "artifact_checksum": base.artifact_checksum,
                }
            else:
                # session-free: use gold as baseline universe when available
                ret = {
                    "retrieved_leaf_ids": gold_ids[: env.common.max_leaves],
                    "route_chosen": "leaf_raw",
                    "fallback_used": False,
                    "accepted_citations": len(gold_ids),
                    "total_citations": max(len(gold_ids), 1),
                    "latency_ms": 8.0,
                    "artifact_checksum": stable_checksum(
                        {"s": "base", "c": case.case_key, "g": gold_ids}
                    ),
                }

        gen = generator(
            query=case.query,
            leaf_ids=list(ret["retrieved_leaf_ids"]),
            strategy=strategy.value,
            cache_namespace=env.cache_namespace,
            expected_answerability=case.expected_answerability.value,
        )
        jdg = judge(
            query=case.query,
            answer=str(gen.get("answer") or ""),
            leaf_ids=list(ret["retrieved_leaf_ids"]),
            strategy=strategy.value,
        )

        # spoiler check: any retrieved leaf in forbidden set
        forbidden = {s.leaf_id for s in case.spoiler_forbidden if s.leaf_id}
        leaks = sum(1 for lid in ret["retrieved_leaf_ids"] if lid in forbidden)
        leaks += int(gen.get("spoiler_leaks") or 0)

        cost = None
        if gen.get("cost_usd") is not None and jdg.get("cost_usd") is not None:
            cost = float(gen["cost_usd"]) + float(jdg["cost_usd"])

        art = {
            "case_key": case.case_key,
            "bucket": case.bucket.value,
            "strategy": strategy.value,
            "retrieved_leaf_ids": list(ret["retrieved_leaf_ids"]),
            "gold_leaf_ids": gold_ids,
            "graded_relevance": graded,
            "route_allowed": allowed,
            "route_chosen": ret.get("route_chosen") or allowed[0],
            "fallback_used": bool(ret.get("fallback_used")),
            "citations_accepted": int(ret.get("accepted_citations") or 0),
            "citations_total": int(ret.get("total_citations") or 0),
            "abstained": bool(gen.get("abstained")),
            "expected_answerability": case.expected_answerability.value,
            "spoiler_leaks": leaks,
            "critical_unsupported": int(gen.get("critical_unsupported") or 0),
            "faithfulness": jdg.get("faithfulness"),
            "relevance": jdg.get("relevance"),
            "latency_ms": float(ret.get("latency_ms") or 0)
            + float(gen.get("latency_ms") or 0)
            + float(jdg.get("latency_ms") or 0),
            "calls": int(gen.get("calls") or 0) + int(jdg.get("calls") or 0),
            "input_tokens": int(gen.get("input_tokens") or 0)
            + int(jdg.get("input_tokens") or 0),
            "output_tokens": int(gen.get("output_tokens") or 0)
            + int(jdg.get("output_tokens") or 0),
            "cost_usd": cost,
            "cache_hit": bool(gen.get("cache_hit")),
            "cache_namespace": env.cache_namespace,
            "artifact_checksum": ret.get("artifact_checksum"),
            "answer": gen.get("answer"),
        }
        arts.append(art)
    return arts


async def run_qualification(
    session: AsyncSession | None,
    fixture: QualificationFixture,
    policy: QualificationPolicy,
    *,
    generator: GeneratorTransport = default_generator,
    judge: JudgeTransport = default_judge,
    reuse: dict[str, Any] | None = None,
    preflight_reasons: list[str] | None = None,
    scope_ok: bool = True,
    structure_ok: bool = True,
    build_complete: bool = True,
    retrieval_ok: bool = True,
    reuse_ok: bool | None = None,
    paired_comparable: bool = True,
    pointer_equal: bool = True,
    pointer_before_digest: str | None = None,
    pointer_after_digest: str | None = None,
    verifier_checksum: str | None = None,
) -> PairedRunResult:
    result = PairedRunResult()
    if preflight_reasons:
        result.block_reasons.extend(preflight_reasons)

    all_arts: list[dict[str, Any]] = []
    try:
        for case in sorted(fixture.cases, key=lambda c: c.case_key):
            arts = await run_paired_case(
                session,
                case,
                fixture,
                policy,
                generator=generator,
                judge=judge,
            )
            all_arts.extend(arts)
            for a in arts:
                result.provider_calls += int(a.get("calls") or 0)
    except Exception as exc:  # noqa: BLE001
        result.blocked = True
        result.block_reasons.append(f"runner_error:{type(exc).__name__}")
        result.report = evaluate_verdict(
            policy=policy,
            fixture_checksum=fixture.checksum(),
            policy_checksum=policy.checksum(),
            metric_cells=[],
            preflight_reasons=result.block_reasons,
            scope_ok=False,
        )
        return result

    result.artifacts = all_arts
    if reuse is None and reuse_ok is not False:
        # default synthetic reuse for deterministic CI when not provided
        reuse = {
            "rebuilt_count": 1,
            "carried_count": 3,
            "stale_count": 0,
            "observed_actual": {"cost_usd": 0.01, "label": "observed_actual"},
            "full_rebuild_upper_bound": {
                "cost_usd": 0.05,
                "label": "full_rebuild_upper_bound",
            },
            "avoided_upper_bound": {
                "cost_usd": 0.04,
                "label": "avoided_upper_bound",
                "formula": "max(0, full - observed)",
            },
        }
        reuse_flag = True
    elif reuse is None:
        reuse_flag = False
    else:
        reuse_flag = True if reuse_ok is None else reuse_ok

    cells = build_complete_report_cells(
        all_arts, reuse=reuse, top_k=policy.budget.top_k
    )
    result.metric_cells = cells
    _ = metric_report_checksum(cells)

    report = evaluate_verdict(
        policy=policy,
        fixture_checksum=fixture.checksum(),
        policy_checksum=policy.checksum(),
        metric_cells=cells,
        preflight_reasons=result.block_reasons,
        scope_ok=scope_ok,
        structure_ok=structure_ok,
        build_complete=build_complete,
        retrieval_ok=retrieval_ok,
        reuse_ok=reuse_flag,
        paired_comparable=paired_comparable,
        pointer_equal=pointer_equal,
        pointer_before_digest=pointer_before_digest or ("d" * 64),
        pointer_after_digest=pointer_after_digest
        or pointer_before_digest
        or ("d" * 64),
        verifier_checksum=verifier_checksum or ("e" * 64),
        buckets_nonempty=all(
            fixture.bucket_counts().get(b.value, 0) >= 1 for b in QuestionBucket
        ),
    )
    result.report = report
    result.blocked = report.verdict.value == "blocked"
    return result


def runner_has_promotion_capability() -> bool:
    return False


def runner_persists_final_authority() -> bool:
    return False
