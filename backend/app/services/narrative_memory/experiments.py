"""Default-off offline hierarchical retrieval experiment runner.

No production HTTP consumer, provider calls, promotion, or active pointer path.
Emits retrieval mechanics only: completed | blocked (never Phase 17 qualification).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.narrative_memory.candidate_reader import (
    CandidateReaderError,
    load_eligible_version,
    load_visible_set_for_route,
    scope_from_eligible,
)
from app.services.narrative_memory.citations import resolve_citations
from app.services.narrative_memory.descent import run_descent
from app.services.narrative_memory.retrieval_contracts import (
    FallbackReasonCode,
    RetrievalBudgets,
    RetrievalQuestion,
    RetrievalRunStatus,
    SafeSourceStatus,
    build_question,
)
from app.services.narrative_memory.retrieval_manifests import (
    build_retrieval_manifest,
    build_safe_trace,
)
from app.services.narrative_memory.routing import (
    ROUTING_POLICY_HASH,
    ROUTING_POLICY_VERSION,
    decide_route_for_scope,
)


class ExperimentDisabledError(RuntimeError):
    pass


class ExperimentInputError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentRequest:
    owner_id: int
    novel_id: int
    version_id: int
    question: RetrievalQuestion
    cutoff_chapter: int
    cutoff_snapshot_hash: str
    full_book_authorized: bool = False
    budgets: RetrievalBudgets | None = None
    require_minimum_citations: int = 0
    expected_source_snapshot_hash: str | None = None
    expected_hierarchy_build_id: str | None = None
    expected_hierarchy_checksum: str | None = None
    expected_manifest_checksum: str | None = None


@dataclass(frozen=True)
class ExperimentResult:
    status: RetrievalRunStatus
    exit_code: int
    report: dict[str, Any]

    def canonical_json(self) -> str:
        return json.dumps(
            self.report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


def assert_experiment_enabled(enabled: bool) -> None:
    if not enabled:
        raise ExperimentDisabledError(
            "narrative_memory_retrieval_experiment_enabled is false; "
            "refusing offline hierarchical retrieval experiment"
        )


async def run_retrieval_experiment(
    session: AsyncSession,
    request: ExperimentRequest,
    *,
    enabled: bool,
) -> ExperimentResult:
    """Execute route → visible → descent → citations → manifest offline path."""

    assert_experiment_enabled(enabled)

    if request.owner_id < 1 or request.novel_id < 1 or request.version_id < 1:
        raise ExperimentInputError("owner_id, novel_id, version_id must be positive")
    if request.cutoff_chapter < 1:
        raise ExperimentInputError("cutoff_chapter must be positive")
    if len(request.cutoff_snapshot_hash) != 64:
        raise ExperimentInputError("cutoff_snapshot_hash must be 64 hex chars")

    try:
        eligible = await load_eligible_version(
            session,
            owner_id=request.owner_id,
            novel_id=request.novel_id,
            version_id=request.version_id,
            expected_source_snapshot_hash=request.expected_source_snapshot_hash,
            expected_hierarchy_build_id=request.expected_hierarchy_build_id,
            expected_hierarchy_checksum=request.expected_hierarchy_checksum,
            expected_manifest_checksum=request.expected_manifest_checksum,
        )
    except CandidateReaderError as exc:
        report = {
            "status": RetrievalRunStatus.BLOCKED.value,
            "blocked_reason": "candidate_ineligible",
            "detail_code": type(exc).__name__,
            "query_hash": request.question.query_hash,
            "owner_id": request.owner_id,
            "novel_id": request.novel_id,
            "version_id": request.version_id,
        }
        return ExperimentResult(
            status=RetrievalRunStatus.BLOCKED, exit_code=2, report=report
        )

    scope = scope_from_eligible(
        eligible,
        cutoff_chapter=request.cutoff_chapter,
        cutoff_snapshot_hash=request.cutoff_snapshot_hash,
        full_book_authorized=request.full_book_authorized,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
        budgets=request.budgets or RetrievalBudgets(),
    )
    route = decide_route_for_scope(request.question, scope)
    visible = await load_visible_set_for_route(
        session, scope, route, request.question, use_cache=True
    )
    descent = await run_descent(session, scope, route)
    outcome = await resolve_citations(
        session,
        scope,
        descent.proposed_leaves,
        require_minimum=request.require_minimum_citations,
    )

    run_status = (
        RetrievalRunStatus.BLOCKED
        if outcome.blocked
        else RetrievalRunStatus.COMPLETED
    )
    source_status = descent.source_status
    if outcome.blocked:
        source_status = SafeSourceStatus.BLOCKED
    elif not outcome.citations and descent.fallback_reason is FallbackReasonCode.NO_ANSWER:
        source_status = SafeSourceStatus.ABSENT
        run_status = RetrievalRunStatus.BLOCKED

    fallback = descent.fallback_reason
    if outcome.dropped and not outcome.citations:
        fallback = FallbackReasonCode.INVALID_LEAF

    manifest = build_retrieval_manifest(
        scope=scope,
        question=request.question,
        route=route,
        traversal=descent.traversal,
        citations=outcome.citations,
        fallback_reason=fallback,
        source_status=source_status,
        run_status=run_status,
        omitted_after_budget=descent.omitted_after_budget
        + visible.omitted_after_budget,
    )
    trace = build_safe_trace(
        route=route,
        source_status=source_status,
        fallback_reason=fallback,
        visible_node_count=len(visible.nodes),
        visible_claim_count=len(visible.claims),
        visible_leaf_count=len(outcome.citations),
        omitted_after_budget=manifest.omitted_after_budget,
        traversal=descent.traversal,
        run_status=run_status,
    )

    report = {
        "status": run_status.value,
        "query_hash": request.question.query_hash,
        "owner_id": request.owner_id,
        "novel_id": request.novel_id,
        "version_id": request.version_id,
        "route": route.model_dump(mode="json"),
        "trace": trace.model_dump(mode="json"),
        "manifest_checksum": manifest.manifest_checksum,
        "manifest": manifest.model_dump(mode="json"),
        "citation_count": len(outcome.citations),
        "dropped_leaves": outcome.dropped,
        "cache_identity_hash": (
            visible.cache.identity_hash if visible.cache is not None else None
        ),
        # Explicitly not a Phase 17 qualification verdict:
        "qualification": None,
        "promotion": None,
    }
    serialized = json.dumps(report, ensure_ascii=False)
    if request.question.normalized_text in serialized:
        raise RuntimeError("sanitized report must not embed question text")
    exit_code = 0 if run_status is RetrievalRunStatus.COMPLETED else 2
    return ExperimentResult(status=run_status, exit_code=exit_code, report=report)


def experiment_request_from_fixture(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    raw_question: str,
    cutoff_chapter: int,
    cutoff_snapshot_hash: str,
    full_book_authorized: bool = False,
    selected_chapter: int | None = None,
    selected_start: int | None = None,
    selected_end: int | None = None,
    fixture_checksum: str | None = None,
    expected_manifest_checksum: str | None = None,
) -> ExperimentRequest:
    question = build_question(
        raw_question,
        selected_chapter=selected_chapter,
        selected_start=selected_start,
        selected_end=selected_end,
    )
    if fixture_checksum is not None and fixture_checksum != question.query_hash:
        raise ExperimentInputError("frozen question fixture checksum mismatch")
    return ExperimentRequest(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        question=question,
        cutoff_chapter=cutoff_chapter,
        cutoff_snapshot_hash=cutoff_snapshot_hash,
        full_book_authorized=full_book_authorized,
        expected_manifest_checksum=expected_manifest_checksum,
    )


def sanitize_public_report(report: dict[str, Any]) -> str:
    """Canonical JSON for adversarial byte-identity comparisons."""

    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
