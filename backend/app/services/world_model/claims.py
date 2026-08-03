"""Strict typed claims for the world-model gate boundary.

Phase 27-01 / REQ-WM-01. Claims are agent/gate inputs: they carry the same
owner/novel/version/cutoff and source EvidenceRefs lineage as durable facts, but
a claim is never persisted by itself. The deterministic gates in ``gates.py``
turn validated claims into immutable ``EventFact`` / ``CausalEdge`` candidates,
or return a stable rejection verdict.

The validator enforces the *shape* of a claim. Semantic authority (co-occurrence
vs. cited cause, spoiler cutoffs, owner identity, stale evidence, authority
upgrades) is enforced by the gates.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from app.services.world_model.contracts import (
    Authority,
    CausalEdgeType,
    Description,
    EffectiveInterval,
    EvidenceRef,
    Key,
    PositiveInt,
    StrictModel,
)

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class EventClaim(StrictModel):
    """Gate input proposing one event fact."""

    claim_kind: Literal["event"] = "event"
    event_key: Key
    title: Key
    description: Description
    authority: Authority
    confidence: Confidence
    effective: EffectiveInterval
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt


class CausalEdgeClaim(StrictModel):
    """Gate input proposing one causal edge.

    ``source_refs`` are the *independent* evidence for the edge itself. An edge
    claim with no independent evidence is co-occurrence, not causality (D-04),
    and is rejected by the evidence gate.
    """

    claim_kind: Literal["causal_edge"] = "causal_edge"
    edge_key: Key
    source_event_key: Key
    target_event_key: Key
    edge_type: CausalEdgeType
    authority: Authority
    confidence: Confidence
    disclosure_cutoff: PositiveInt
    source_refs: tuple[EvidenceRef, ...]
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt

    @property
    def has_independent_evidence(self) -> bool:
        return bool(self.source_refs)
