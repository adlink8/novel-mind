"""Canonical safe retrieval manifests and checksums."""

from __future__ import annotations

from app.services.narrative_memory.retrieval_contracts import (
    FallbackReasonCode,
    LeafCitation,
    RetrievalManifest,
    RetrievalQuestion,
    RetrievalRunStatus,
    RetrievalScope,
    RouteDecision,
    SafeSourceStatus,
    SafeTrace,
    TraversalStep,
    canonical_retrieval_json,
    retrieval_component_hash,
    scope_hash,
)


MANIFEST_SCHEMA = "narrative-memory-retrieval-manifest.v1"


def build_retrieval_manifest(
    *,
    scope: RetrievalScope,
    question: RetrievalQuestion,
    route: RouteDecision,
    traversal: tuple[TraversalStep, ...] | list[TraversalStep],
    citations: tuple[LeafCitation, ...] | list[LeafCitation],
    fallback_reason: FallbackReasonCode,
    source_status: SafeSourceStatus,
    run_status: RetrievalRunStatus,
    omitted_after_budget: int,
) -> RetrievalManifest:
    steps = tuple(traversal)
    cites = tuple(citations)
    payload = {
        "schema_version": MANIFEST_SCHEMA,
        "scope_hash": scope_hash(scope),
        "query_hash": question.query_hash,
        "policy_hash": scope.policy_hash,
        "candidate_manifest_checksum": scope.candidate_manifest_checksum,
        "hierarchy_build_id": scope.hierarchy_build_id,
        "hierarchy_checksum": scope.hierarchy_checksum,
        "source_snapshot_hash": scope.source_snapshot_hash,
        "cutoff_snapshot_hash": scope.cutoff.snapshot_hash,
        "route": route.model_dump(mode="json"),
        "fallback_reason": fallback_reason.value,
        "source_status": source_status.value,
        "run_status": run_status.value,
        "traversal": [s.model_dump(mode="json") for s in steps],
        "citations": [c.model_dump(mode="json") for c in cites],
        "omitted_after_budget": omitted_after_budget,
    }
    checksum = retrieval_component_hash("retrieval-manifest", payload)
    return RetrievalManifest(
        schema_version=MANIFEST_SCHEMA,  # type: ignore[arg-type]
        scope_hash=payload["scope_hash"],  # type: ignore[arg-type]
        query_hash=question.query_hash,
        policy_hash=scope.policy_hash,
        candidate_manifest_checksum=scope.candidate_manifest_checksum,
        hierarchy_build_id=scope.hierarchy_build_id,
        hierarchy_checksum=scope.hierarchy_checksum,
        source_snapshot_hash=scope.source_snapshot_hash,
        cutoff_snapshot_hash=scope.cutoff.snapshot_hash,
        route=route,
        fallback_reason=fallback_reason,
        source_status=source_status,
        run_status=run_status,
        traversal=steps,
        citations=cites,
        omitted_after_budget=omitted_after_budget,
        manifest_checksum=checksum,
    )


def build_safe_trace(
    *,
    route: RouteDecision,
    source_status: SafeSourceStatus,
    fallback_reason: FallbackReasonCode,
    visible_node_count: int,
    visible_claim_count: int,
    visible_leaf_count: int,
    omitted_after_budget: int,
    traversal: tuple[TraversalStep, ...] | list[TraversalStep],
    run_status: RetrievalRunStatus,
) -> SafeTrace:
    return SafeTrace(
        route=route,
        source_status=source_status,
        fallback_reason=fallback_reason,
        visible_node_count=visible_node_count,
        visible_claim_count=visible_claim_count,
        visible_leaf_count=visible_leaf_count,
        omitted_after_budget=omitted_after_budget,
        traversal=tuple(traversal),
        run_status=run_status,
    )


def manifest_is_leak_free(manifest: RetrievalManifest) -> bool:
    """Heuristic scan: serialized manifest must not contain leaky keys."""

    text = canonical_retrieval_json(manifest)
    forbidden = (
        "display_label",
        "cache_key",
        "hidden_future",
        "raw_question",
        "rationale",
        "similarity",
        "embedding",
        "chat_text",
        "provider",
    )
    return not any(token in text for token in forbidden)
