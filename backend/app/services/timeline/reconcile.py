"""Deterministic cross-chapter timeline reconciliation around one quality-tier call."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.timeline import EventCandidate
from app.services.timeline.budget import BudgetExceeded, BudgetGate
from app.services.timeline.model_gateway import ModelDeployment, TimelineModelGateway

EdgeType = Literal["causes", "triggers", "responds_to", "blocks"]


@dataclass(frozen=True)
class CausalProposal:
    source_id: str
    target_id: str
    edge_type: EdgeType
    evidence_ids: list[str]
    confidence: float


@dataclass(frozen=True)
class ReconcileInput:
    duplicate_groups: list[list[str]] = field(default_factory=list)
    story_constraints: list[tuple[str, str, str]] = field(default_factory=list)
    causal_edges: list[CausalProposal] = field(default_factory=list)


class ReconciliationCausalProposalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: str = Field(min_length=1, max_length=160)
    target_id: str = Field(min_length=1, max_length=160)
    edge_type: EdgeType
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ReconciliationOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    duplicate_groups: list[list[str]]
    story_constraints: list[tuple[str, str, Literal["before", "after", "simultaneous"]]]
    causal_edges: list[ReconciliationCausalProposalModel]

    def as_input(self) -> ReconcileInput:
        return ReconcileInput(
            duplicate_groups=self.duplicate_groups,
            story_constraints=[tuple(item) for item in self.story_constraints],
            causal_edges=[CausalProposal(**item.model_dump()) for item in self.causal_edges],
        )


@dataclass(frozen=True)
class ReconciledEvent:
    logical_event_id: str
    source_candidate_ids: tuple[str, ...]
    title: str
    participant_mentions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    narrative_order: tuple[int, int]
    story_rank: int | None


@dataclass(frozen=True)
class ReconciledEdge:
    source_event_id: str
    target_event_id: str
    edge_type: EdgeType
    evidence_ids: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class ReconcileResult:
    events: tuple[ReconciledEvent, ...]
    edges: tuple[ReconciledEdge, ...]
    conflicts: tuple[str, ...]


Transport = Callable[[dict], Awaitable[ReconcileInput]]


class TimelineReconciler:
    """The script reserves budget, validates proposals, and owns graph ordering."""

    def __init__(self, *, deployment: ModelDeployment, budget: BudgetGate,
                 gateway: TimelineModelGateway | None = None,
                 transport: Transport | None = None) -> None:
        if gateway is None and transport is None:
            raise ValueError("reconciliation requires the strict gateway")
        self.gateway = gateway
        # Kept only for deterministic unit adapters; production worker always supplies gateway.
        self._test_transport = transport
        self.deployment = deployment
        self.budget = budget
        self.run_status = "running"

    async def reconcile(self, *, run_id: int, events: list[EventCandidate],
                        cache_key: str | None = None) -> ReconcileResult:
        if "quality" not in self.deployment.model_id.lower():
            raise ValueError("reconciliation requires an explicitly qualified quality deployment")
        if self.gateway is not None:
            try:
                gateway_result = await self.gateway.generate(
                    deployment=self.deployment,
                    schema=ReconciliationOutputModel,
                    messages=[
                        {"role": "system", "content": "Reconcile only supplied event IDs and evidence."},
                        {"role": "user", "content": json.dumps([
                            event.model_dump(mode="json") for event in events
                        ], sort_keys=True)},
                    ],
                    budget=self.budget,
                    run_id=run_id,
                    stage_key="cross_chapter_reconcile:book",
                    cache_key=cache_key,
                    max_input_tokens=max(512, sum(len(e.description) for e in events) * 2),
                    max_output_tokens=4000,
                )
            except BudgetExceeded:
                self.run_status = "paused_budget"
                raise
            proposal = gateway_result.output.as_input()
        else:
            reservation_key = f"reconcile:{run_id}:attempt:1"
            try:
                reservation = self.budget.reserve(
                    reservation_key,
                    input_tokens=max(512, sum(len(e.description) for e in events) * 2),
                    output_tokens=2048,
                    input_price_per_million=self.deployment.input_price_per_million,
                    output_price_per_million=self.deployment.output_price_per_million,
                )
            except BudgetExceeded:
                self.run_status = "paused_budget"
                raise
            proposal = await self._test_transport({
                "run_id": run_id,
                "deployment": self.deployment.lineage,
                "reservation_status": reservation.status,
                "events": events,
            })
        return self._materialize(events, proposal)

    @staticmethod
    def _materialize(events: list[EventCandidate], proposal: ReconcileInput) -> ReconcileResult:
        by_id = {event.candidate_id: event for event in events}
        canonical = {candidate_id: candidate_id for candidate_id in by_id}
        groups: dict[str, list[str]] = {candidate_id: [candidate_id] for candidate_id in by_id}
        for raw_group in proposal.duplicate_groups:
            members = [candidate_id for candidate_id in raw_group if candidate_id in by_id]
            if len(members) < 2:
                continue
            root = min(members, key=lambda key: (
                by_id[key].narrative_chapter_number, by_id[key].narrative_index, key
            ))
            merged = sorted(set(sum((groups.pop(canonical[item], []) for item in members), [])))
            groups[root] = merged
            for item in merged:
                canonical[item] = root

        exact_sorted = sorted(
            (event for event in events if event.story_time.exact_time is not None),
            key=lambda event: event.story_time.exact_time,
        )
        constraints = [(canonical.get(a, a), canonical.get(b, b))
                       for a, b, relation in proposal.story_constraints if relation == "before"]
        constraints += [(canonical.get(b, b), canonical.get(a, a))
                        for a, b, relation in proposal.story_constraints if relation == "after"]
        constraints += [(canonical[exact_sorted[i].candidate_id], canonical[exact_sorted[i + 1].candidate_id])
                        for i in range(len(exact_sorted) - 1)]
        ranks, conflicts = _topological_ranks(groups, constraints)

        materialized = []
        evidence_by_root: dict[str, set[str]] = {}
        for root, members in groups.items():
            source = min((by_id[item] for item in members),
                         key=lambda event: (event.narrative_chapter_number, event.narrative_index))
            evidence = {ref.evidence_id for item in members for ref in by_id[item].evidence}
            evidence_by_root[root] = evidence
            materialized.append(ReconciledEvent(
                root, tuple(members), source.title,
                tuple(sorted({p.mention for item in members for p in by_id[item].participants})),
                tuple(sorted(evidence)),
                (source.narrative_chapter_number, source.narrative_index), ranks.get(root),
            ))
        materialized.sort(key=lambda event: event.narrative_order)

        edges = []
        for edge in proposal.causal_edges:
            source, target = canonical.get(edge.source_id), canonical.get(edge.target_id)
            if not source or not target or source == target:
                continue
            refs = set(edge.evidence_ids)
            if not (refs & evidence_by_root[source] and refs & evidence_by_root[target]):
                continue
            edges.append(ReconciledEdge(source, target, edge.edge_type,
                                        tuple(sorted(refs)), edge.confidence))
        return ReconcileResult(tuple(materialized), tuple(edges), tuple(conflicts))


def _topological_ranks(nodes: dict[str, list[str]], constraints: list[tuple[str, str]]) -> tuple[dict[str, int], list[str]]:
    graph = {node: set() for node in nodes}
    indegree = {node: 0 for node in nodes}
    for source, target in constraints:
        if source == target or source not in graph or target not in graph or target in graph[source]:
            continue
        graph[source].add(target)
        indegree[target] += 1
    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for target in sorted(graph[node]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(nodes):
        cyclic = sorted(node for node, degree in indegree.items() if degree > 0)
        return {}, [f"contradictory chronology cycle: {','.join(cyclic)}"]
    return {node: rank for rank, node in enumerate(ordered)}, []
