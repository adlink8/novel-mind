"""Read-only cutoff/POV epistemic query engine (REQ-WM-02, D-05/D-06).

This module is pure and in-memory: it operates on immutable
``EpistemicClaim`` sequences (unit-testable without a database). The durable
DB-backed equivalent is ``knowledge_queries.py``.

Query order is strictly scoped first, then filtered (D-05):

1. Scope: owner / novel / version / subject / cutoff / POV.
2. Disclosure: a claim is visible only when ``known_at <= cutoff`` AND
   ``disclosure_cutoff <= cutoff`` — hidden knowledge never leaks early.
3. Authority / candidate filter: optional authority allowlist; candidate-only
   claims are reported as ``candidate_only``, never promoted silently.

Abstention is a first-class result: when a character has no knowledge at a
cutoff/POV, the engine returns ``ABSTAINED`` with no fabricated claim (D-06).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.world_model.contracts import Authority, EvidenceRef, GateStatus
from app.services.world_model.knowledge import (
    EpistemicAspect,
    EpistemicClaim,
    EpistemicStatus,
    KnowledgeResultStatus,
    PovKind,
)


@dataclass(frozen=True)
class EpistemicAnswer:
    """Every query returns one of these; abstention fabricates nothing."""

    status: KnowledgeResultStatus
    subject: str
    claims: tuple[EpistemicClaim, ...]
    evidence: tuple[EvidenceRef, ...]
    has_approval: bool
    message: str

    @classmethod
    def abstain(cls, subject: str, message: str) -> "EpistemicAnswer":
        return cls(
            status=KnowledgeResultStatus.ABSTAINED,
            subject=subject,
            claims=(),
            evidence=(),
            has_approval=False,
            message=message,
        )


class EpistemicQueryEngine:
    """Pure in-memory, owner-scoped, cutoff/POV-aware epistemic query API."""

    def __init__(self, claims: Iterable[EpistemicClaim]) -> None:
        self._claims = tuple(claims)

    # ------------------------------------------------------------------ scope

    def _scope(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int | None = None,
    ) -> list[EpistemicClaim]:
        rows = [
            claim
            for claim in self._claims
            if claim.owner_id == owner_id and claim.novel_id == novel_id
        ]
        if version_id is not None:
            rows = [claim for claim in rows if claim.version_id == version_id]
        return rows

    # ------------------------------------------------------------------ query

    def query_character_knowledge(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        subject: str,
        cutoff: int,
        pov: str | None = None,
        authorities: frozenset[Authority] | None = None,
        aspect: EpistemicAspect | None = None,
    ) -> EpistemicAnswer:
        """What does ``subject`` know at ``cutoff``, scoped to owner/novel/version?

        POV filters to claims authored from that perspective (omniscient claims
        are visible to any POV query). Hidden knowledge stays hidden until its
        ``disclosure_cutoff``, so it never leaks early.
        """
        claims = [
            claim
            for claim in self._scope(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            if claim.subject == subject
            and claim.known_at <= cutoff
            and claim.disclosure_cutoff <= cutoff
        ]
        if pov is not None:
            claims = [
                claim
                for claim in claims
                if claim.pov == pov or claim.pov_kind == PovKind.OMNISCIENT
            ]
        if aspect is not None:
            claims = [claim for claim in claims if claim.aspect == aspect]
        if authorities is not None:
            claims = [claim for claim in claims if claim.authority in authorities]

        claims.sort(key=lambda claim: (claim.known_at, claim.knowledge_key))
        return self._answer(subject, claims)

    def query_character_history(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        subject: str,
        aspect: EpistemicAspect | None = None,
    ) -> tuple[EpistemicClaim, ...]:
        """Full state/goal/motivation/knowledge history for one subject.

        History is authoritative (author view): no cutoff/POV filter is applied
        so mistaken beliefs, hidden knowledge and contradictions all remain
        queryable. Sorted by story-time ``known_at``.
        """
        claims = [
            claim
            for claim in self._scope(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            if claim.subject == subject
        ]
        if aspect is not None:
            claims = [claim for claim in claims if claim.aspect == aspect]
        claims.sort(key=lambda claim: (claim.known_at, claim.knowledge_key))
        return tuple(claims)

    def query_lineage(
        self, *, owner_id: int, novel_id: int, knowledge_key: str
    ) -> tuple[EpistemicClaim, ...]:
        """Full version lineage of one logical knowledge chain, oldest first."""
        rows = [
            claim
            for claim in self._scope(owner_id=owner_id, novel_id=novel_id)
            if claim.knowledge_key == knowledge_key
            or knowledge_key in claim.lineage
        ]
        rows.sort(
            key=lambda claim: (claim.version_id, claim.known_at, claim.knowledge_key)
        )
        return tuple(rows)

    def query_by_status(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        status: EpistemicStatus,
    ) -> tuple[EpistemicClaim, ...]:
        """All claims with one epistemic label (mistaken/hidden/contradiction)."""
        rows = [
            claim
            for claim in self._scope(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            if claim.epistemic_status == status
        ]
        rows.sort(key=lambda claim: (claim.known_at, claim.knowledge_key))
        return tuple(rows)

    # ---------------------------------------------------------------- helpers

    def _answer(self, subject: str, claims: list[EpistemicClaim]) -> EpistemicAnswer:
        if not claims:
            return EpistemicAnswer.abstain(
                subject,
                "no knowledge at this cutoff/POV — abstaining, nothing fabricated",
            )
        approved = [
            claim for claim in claims if claim.gate_status == GateStatus.PASSED
        ]
        evidence = tuple(
            ref for claim in claims for ref in claim.source_refs
        )
        status = (
            KnowledgeResultStatus.ANSWERED
            if approved
            else KnowledgeResultStatus.CANDIDATE_ONLY
        )
        return EpistemicAnswer(
            status=status,
            subject=subject,
            claims=tuple(claims),
            evidence=evidence,
            has_approval=bool(approved),
            message=(
                "knowledge answered with evidence"
                if status == KnowledgeResultStatus.ANSWERED
                else "claims are candidate-only, awaiting approval"
            ),
        )


def visible_at_cutoff(claim: EpistemicClaim, cutoff: int, pov: str | None) -> bool:
    """Disclosure helper shared by the durable query layer (D-05).

    A claim is visible only when both ``known_at`` and ``disclosure_cutoff``
    are at or before the cutoff. When ``pov`` is given, only claims authored
    from that perspective (plus omniscient claims) are visible.
    """
    if claim.known_at > cutoff or claim.disclosure_cutoff > cutoff:
        return False
    if pov is None:
        return True
    return claim.pov == pov or claim.pov_kind == PovKind.OMNISCIENT
