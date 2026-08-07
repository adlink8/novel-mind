"""Deterministic evidence-gated world-model gates (REQ-WM-01, D-01..D-04).

The gate is the *only* path from a claim to a durable candidate. It is pure and
fail-closed: rejected claims produce stable, auditable verdicts and are never
materialized. Co-occurrence is not causality (D-04): a causal edge requires
independent evidence. Temporal conflicts are detected and preserved, never
resolved by overwrite. No gate can silently upgrade ``probable_inference`` /
``literary_interpretation`` / ``user_interpretation`` into ``canon_fact``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.world_model.claims import CausalEdgeClaim, EventClaim
from app.services.world_model.contracts import (
    Authority,
    CausalEdge,
    ConflictKind,
    EventFact,
    GateStatus,
    WorldModelCandidateProjection,
    WorldModelConflict,
    build_projection,
    event_checksum,
)


class GateReason(StrEnum):
    GATE_PASSED = "gate_passed"
    NO_EVIDENCE = "no_evidence"
    CO_OCCURRENCE_ONLY = "co_occurrence_only"
    STALE_EVIDENCE = "stale_evidence"
    WRONG_OWNER = "wrong_owner"
    STALE_VERSION = "stale_version"
    SPOILER_CUTOFF = "spoiler_cutoff"
    EVIDENCE_BEYOND_CUTOFF = "evidence_beyond_cutoff"
    MISSING_APPROVAL = "missing_approval"
    AUTHORITY_UPGRADE = "authority_upgrade"
    UNKNOWN_ENDPOINT = "unknown_endpoint"


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    reason_code: GateReason
    message: str

    @classmethod
    def ok(cls, message: str = "gate passed") -> "GateVerdict":
        return cls(passed=True, reason_code=GateReason.GATE_PASSED, message=message)

    @classmethod
    def reject(cls, reason_code: GateReason, message: str) -> "GateVerdict":
        return cls(passed=False, reason_code=reason_code, message=message)


@dataclass(frozen=True)
class EventGateResult:
    fact: EventFact | None
    verdicts: tuple[GateVerdict, ...]


@dataclass(frozen=True)
class EdgeGateResult:
    edge: CausalEdge | None
    verdicts: tuple[GateVerdict, ...]


class WorldModelGate:
    """Scope-locked gate for one owner/novel/version projection submission.

    ``source_snapshot_hash`` is the frozen source package the evidence must
    belong to; ``disclosure_cutoff`` is the authorized reader cutoff (D-05);
    ``approvals`` is the set of authorities explicitly confirmed for this
    submission (D-06 user interpretation, plus canon_fact publication).
    """

    def __init__(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        source_snapshot_hash: str,
        disclosure_cutoff: int,
        approvals: frozenset[Authority] = frozenset(),
    ) -> None:
        self.owner_id = owner_id
        self.novel_id = novel_id
        self.version_id = version_id
        self.source_snapshot_hash = source_snapshot_hash
        self.disclosure_cutoff = disclosure_cutoff
        self.approvals = approvals

    # ------------------------------------------------------------------ scope

    def _scope_verdict(self, claim: EventClaim | CausalEdgeClaim) -> GateVerdict | None:
        if claim.owner_id != self.owner_id or claim.novel_id != self.novel_id:
            return GateVerdict.reject(
                GateReason.WRONG_OWNER,
                f"claim owner/novel scope {claim.owner_id}/{claim.novel_id} "
                f"does not match gate scope {self.owner_id}/{self.novel_id}",
            )
        if claim.version_id != self.version_id:
            return GateVerdict.reject(
                GateReason.STALE_VERSION,
                f"claim version {claim.version_id} is not the gated "
                f"version {self.version_id}",
            )
        return None

    # ------------------------------------------------------------------ gates

    def _evidence_verdict(self, refs) -> GateVerdict | None:
        for ref in refs:
            if ref.source_snapshot_hash != self.source_snapshot_hash:
                return GateVerdict.reject(
                    GateReason.STALE_EVIDENCE,
                    f"evidence {ref.evidence_id} is stale: snapshot "
                    f"{ref.source_snapshot_hash[:8]}… does not match the frozen "
                    f"source package {self.source_snapshot_hash[:8]}…",
                )
        return None

    def _spoiler_verdict(
        self, claim: EventClaim | CausalEdgeClaim
    ) -> GateVerdict | None:
        if claim.disclosure_cutoff > self.disclosure_cutoff:
            return GateVerdict.reject(
                GateReason.SPOILER_CUTOFF,
                f"disclosure cutoff {claim.disclosure_cutoff} is beyond the "
                f"authorized cutoff {self.disclosure_cutoff}",
            )
        for ref in claim.source_refs:
            if ref.chapter_number > claim.disclosure_cutoff:
                return GateVerdict.reject(
                    GateReason.EVIDENCE_BEYOND_CUTOFF,
                    f"evidence {ref.evidence_id} is at chapter "
                    f"{ref.chapter_number}, after the claim cutoff "
                    f"{claim.disclosure_cutoff}",
                )
        return None

    def _authority_verdict(
        self, claim: EventClaim | CausalEdgeClaim
    ) -> GateVerdict | None:
        if (
            claim.authority == Authority.CANON_FACT
            and Authority.CANON_FACT not in self.approvals
        ):
            return GateVerdict.reject(
                GateReason.AUTHORITY_UPGRADE,
                "canon_fact requires explicit approval; inference/interpretation "
                "must never serialize as canon_fact (D-01)",
            )
        if (
            claim.authority == Authority.USER_INTERPRETATION
            and Authority.USER_INTERPRETATION not in self.approvals
        ):
            return GateVerdict.reject(
                GateReason.MISSING_APPROVAL,
                "user_interpretation requires explicit confirmation (D-06)",
            )
        return None

    # ---------------------------------------------------------------- events

    def validate_event(self, claim: EventClaim) -> EventGateResult:
        verdicts: list[GateVerdict] = []
        scope = self._scope_verdict(claim)
        if scope is not None:
            verdicts.append(scope)
            return EventGateResult(None, tuple(verdicts))

        gates = (
            self._evidence_verdict(claim.source_refs),
            self._spoiler_verdict(claim),
            self._authority_verdict(claim),
        )
        for verdict in gates:
            if verdict is not None:
                verdicts.append(verdict)
        if any(not verdict.passed for verdict in verdicts):
            return EventGateResult(None, tuple(verdicts))

        verdicts.append(GateVerdict.ok("event gate passed"))
        fact = EventFact(
            event_key=claim.event_key,
            title=claim.title,
            description=claim.description,
            authority=claim.authority,
            confidence=claim.confidence,
            effective=claim.effective,
            disclosure_cutoff=claim.disclosure_cutoff,
            source_refs=claim.source_refs,
            gate_status=GateStatus.PASSED,
            gate_reason=None,
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
        )
        return EventGateResult(fact, tuple(verdicts))

    # ------------------------------------------------------------------ edges

    def validate_edge(
        self, claim: CausalEdgeClaim, events_by_key: dict[str, EventFact]
    ) -> EdgeGateResult:
        verdicts: list[GateVerdict] = []
        scope = self._scope_verdict(claim)
        if scope is not None:
            verdicts.append(scope)
            return EdgeGateResult(None, tuple(verdicts))

        if (
            claim.source_event_key not in events_by_key
            or claim.target_event_key not in events_by_key
        ):
            verdicts.append(
                GateVerdict.reject(
                    GateReason.UNKNOWN_ENDPOINT,
                    f"edge endpoints {claim.source_event_key}→"
                    f"{claim.target_event_key} are not projection-local events",
                )
            )
            return EdgeGateResult(None, tuple(verdicts))

        gates = (
            self._evidence_verdict(claim.source_refs),
            self._spoiler_verdict(claim),
            self._authority_verdict(claim),
        )
        for verdict in gates:
            if verdict is not None:
                verdicts.append(verdict)

        # D-04: causality requires independent evidence, not co-occurrence.
        if not claim.has_independent_evidence:
            verdicts.append(
                GateVerdict.reject(
                    GateReason.CO_OCCURRENCE_ONLY,
                    "causal edge without independent evidence is co-occurrence, "
                    "not causality (D-04)",
                )
            )

        if any(not verdict.passed for verdict in verdicts):
            return EdgeGateResult(None, tuple(verdicts))

        verdicts.append(GateVerdict.ok("edge gate passed"))
        edge = CausalEdge(
            edge_key=claim.edge_key,
            source_event_key=claim.source_event_key,
            target_event_key=claim.target_event_key,
            edge_type=claim.edge_type,
            authority=claim.authority,
            confidence=claim.confidence,
            disclosure_cutoff=claim.disclosure_cutoff,
            source_refs=claim.source_refs,
            gate_status=GateStatus.PASSED,
            gate_reason=None,
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
        )
        return EdgeGateResult(edge, tuple(verdicts))


# ---------------------------------------------------------------------------
# Conflict detection (preserve, never overwrite)
# ---------------------------------------------------------------------------


def detect_conflicts(
    events: list[EventFact],
    edges: list[CausalEdge],
) -> tuple[WorldModelConflict, ...]:
    """Return preserved conflicts among the durable candidate rows.

    - Assertion conflict: two rows carry the same ``event_key`` with different
      canonical content (a version lineage disagreement). Both rows are kept.
    - Temporal conflict: an edge asserts cause→effect ordering that violates the
      effective intervals (effect starts before cause). The edge is kept but
      flagged.
    """
    conflicts: list[WorldModelConflict] = []
    by_key: dict[str, EventFact] = {}
    for event in events:
        if event.event_key in by_key:
            prior = by_key[event.event_key]
            if event_checksum(event) != event_checksum(prior):
                conflicts.append(
                    WorldModelConflict(
                        conflict_key=f"assert:{event.event_key}",
                        kind=ConflictKind.ASSERTION_CONFLICT,
                        involved_keys=(event.event_key,),
                        description=(
                            f"two rows assert different content for event "
                            f"'{event.event_key}'; both versions are preserved"
                        ),
                    )
                )
        else:
            by_key[event.event_key] = event

    events_by_key = {event.event_key: event for event in events}
    for edge in edges:
        source = events_by_key.get(edge.source_event_key)
        target = events_by_key.get(edge.target_event_key)
        if source is None or target is None:
            continue
        source_start = source.effective.start
        target_start = target.effective.start
        if (
            source_start is not None
            and target_start is not None
            and target_start < source_start
        ):
            conflicts.append(
                WorldModelConflict(
                    conflict_key=f"temporal:{edge.edge_key}",
                    kind=ConflictKind.TEMPORAL_CONFLICT,
                    involved_keys=(edge.source_event_key, edge.target_event_key),
                    description=(
                        f"edge '{edge.edge_key}' asserts cause→effect but the "
                        f"effect ({target_start}) starts before the cause "
                        f"({source_start}); both rows and the edge are preserved"
                    ),
                )
            )
    return tuple(conflicts)


def build_candidate(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    events: list[EventFact],
    edges: list[CausalEdge],
) -> WorldModelCandidateProjection:
    """Gate-blessed immutable candidate projection with preserved conflicts."""
    conflicts = detect_conflicts(events, edges)
    return build_projection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        events=events,
        edges=edges,
        conflicts=list(conflicts),
    )
