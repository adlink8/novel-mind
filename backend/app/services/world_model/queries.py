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

from app.services.queryplan.adapters import (
    ReaderContext,
    WorldProjectionOutcome,
    WorldProjectionUnavailableError,
)
from app.services.queryplan.contracts import (
    WorldProjectionItem,
    leaf_evidence_key,
)
from app.services.queryplan.schemas import EvidenceRef as QueryPlanEvidenceRef
from app.services.world_model.contracts import Authority, EvidenceRef, GateStatus
from app.services.world_model.knowledge import (
    EpistemicAspect,
    EpistemicClaim,
    EpistemicStatus,
    KnowledgeResultStatus,
    PovKind,
    SourceKind,
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


@dataclass(frozen=True)
class WorldProjectionAnswer:
    """One cutoff/POV-filtered world projection over an owner/novel/version.

    ``items`` are the original candidate claims visible at the cutoff/POV and
    ``overrides`` are the isolated user-interpretation claims (D-06): the two
    are never merged. ``available`` is True only when approved candidate
    evidence exists; a missing or fully hidden projection is explicitly
    ``ABSTAINED`` — never an empty success.
    """

    status: KnowledgeResultStatus
    available: bool
    cutoff: int
    items: tuple[EpistemicClaim, ...]
    overrides: tuple[EpistemicClaim, ...]
    authorities: frozenset[Authority]
    message: str


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

    def query_world_projection(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        cutoff: int,
        pov: str | None = None,
        authorities: frozenset[Authority] | None = None,
    ) -> WorldProjectionAnswer:
        """Cutoff/POV-filtered world projection for an owner/novel/version scope.

        Scoped first, then disclosure-filtered (D-05): a claim is visible only
        when ``known_at <= cutoff`` AND ``disclosure_cutoff <= cutoff``. Hidden
        knowledge (``disclosure_cutoff > cutoff``) never leaks. Authority labels
        are preserved — an optional allowlist filters, it never relabels.
        ``user_interpretation`` claims are isolated into ``overrides`` (D-06)
        and never merged with the original candidate projection.
        """
        claims = self._scope(owner_id=owner_id, novel_id=novel_id, version_id=version_id)
        if authorities is not None:
            claims = [claim for claim in claims if claim.authority in authorities]
        visible = [
            claim for claim in claims if visible_at_cutoff(claim, cutoff, pov)
        ]
        candidates = [
            claim
            for claim in visible
            if claim.authority != Authority.USER_INTERPRETATION
        ]
        overrides = [
            claim
            for claim in visible
            if claim.authority == Authority.USER_INTERPRETATION
        ]
        if not candidates and not overrides:
            return WorldProjectionAnswer(
                status=KnowledgeResultStatus.ABSTAINED,
                available=False,
                cutoff=cutoff,
                items=(),
                overrides=(),
                authorities=frozenset(),
                message=(
                    "no world projection visible at this cutoff/POV — "
                    "abstaining, nothing fabricated"
                ),
            )
        approved = [
            claim for claim in candidates if claim.gate_status == GateStatus.PASSED
        ]
        status = (
            KnowledgeResultStatus.ANSWERED
            if approved
            else KnowledgeResultStatus.CANDIDATE_ONLY
        )
        return WorldProjectionAnswer(
            status=status,
            available=bool(approved),
            cutoff=cutoff,
            items=tuple(candidates),
            overrides=tuple(overrides),
            authorities=frozenset(claim.authority for claim in candidates),
            message=(
                "world projection answered with evidence"
                if status == KnowledgeResultStatus.ANSWERED
                else "world projection is candidate-only, awaiting approval"
            ),
        )

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


# ---------------------------------------------------------------------------
# QueryPlan world projection wiring (REQ-WM-04, D-05/D-06)
# ---------------------------------------------------------------------------


def _queryplan_evidence_ref(ref: EvidenceRef) -> QueryPlanEvidenceRef:
    """Map a world-model leaf EvidenceRef into the QueryPlan leaf contract."""
    return QueryPlanEvidenceRef(
        chapter_id=ref.chapter_id,
        chapter_number=ref.chapter_number,
        source_start=ref.source_start,
        source_end=ref.source_end,
        content_hash=ref.content_hash,
        source_snapshot_hash=ref.source_snapshot_hash,
    )


def claim_to_world_projection_item(
    claim: EpistemicClaim, *, kind: str, ref: EvidenceRef | None = None
) -> WorldProjectionItem:
    """Serialize one epistemic claim into the shared world projection contract.

    The authority label is carried verbatim (D-01); the evidence key is the
    leaf allowlist key bound to the frozen snapshot (D-07/D-08).
    """
    leaf = ref if ref is not None else claim.source_refs[0]
    return WorldProjectionItem(
        claim_key=claim.knowledge_key,
        kind=kind,
        subject=claim.subject,
        aspect=claim.aspect.value,
        proposition=claim.proposition,
        authority=claim.authority.value,
        known_at=claim.known_at,
        disclosure_cutoff=claim.disclosure_cutoff,
        pov=claim.pov,
        gate_status=claim.gate_status.value,
        approved=claim.gate_status == GateStatus.PASSED,
        is_override=claim.authority == Authority.USER_INTERPRETATION,
        evidence_key=leaf_evidence_key(
            chapter_id=leaf.chapter_id,
            source_start=leaf.source_start,
            source_end=leaf.source_end,
            content_hash=leaf.content_hash,
        ),
        chapter_id=leaf.chapter_id,
        chapter_number=leaf.chapter_number,
        source_start=leaf.source_start,
        source_end=leaf.source_end,
        content_hash=leaf.content_hash,
        source_snapshot_hash=leaf.source_snapshot_hash,
        lineage=tuple(claim.lineage),
    )


async def world_projection_reader(
    claims: Iterable[EpistemicClaim],
    *,
    context: ReaderContext,
    kind: str = "character",
    pov: str | None = None,
    authorities: frozenset[Authority] | None = None,
) -> WorldProjectionOutcome | None:
    """Reader callable body: map scoped epistemic claims to a projection outcome.

    Returns ``None`` when no projection exists at all for the scope so the
    adapter reports explicit ``unavailable`` — a missing projection is never an
    empty success (D-05). The reader only ever serves the frozen snapshot scope
    carried by ``context``; a mismatch raises and fails closed. Reader Chat /
    user conversation claims are not fact sources (D-06) and are excluded
    defense-in-depth even if they were ever materialized.
    """
    claims = tuple(claims)
    if not claims:
        return None
    for claim in claims:
        if claim.source_kind in (
            SourceKind.READER_CHAT,
            SourceKind.USER_CONVERSATION,
        ):
            raise WorldProjectionUnavailableError(
                "Reader Chat / user conversation is never a world-model fact "
                "source and can never enter a world projection (D-06)"
            )
        if claim.source_refs and any(
            ref.source_snapshot_hash != context.snapshot_hash
            for ref in claim.source_refs
        ):
            raise WorldProjectionUnavailableError(
                "world projection claims escape the frozen snapshot lineage "
                "(owner/novel/version/snapshot boundary)"
            )
    owner_id = claims[0].owner_id
    novel_id = claims[0].novel_id
    if owner_id != context.owner_id or novel_id != context.novel_id:
        raise WorldProjectionUnavailableError(
            "world projection claims escape the reader scope "
            "(owner/novel boundary)"
        )
    answer = EpistemicQueryEngine(claims).query_world_projection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=context.version_id,
        cutoff=int(context.through_chapter),
        pov=pov,
        authorities=authorities,
    )
    if answer.status == KnowledgeResultStatus.ABSTAINED:
        # Nothing is visible at this cutoff/POV (D-05) — explicit abstention,
        # never empty-success. The projection exists but its claims are hidden.
        return WorldProjectionOutcome(
            status="abstained",
            cutoff=int(context.through_chapter),
            refs=(),
        )
    items = tuple(
        claim_to_world_projection_item(claim, kind=kind)
        for claim in answer.items
    )
    overrides = tuple(
        claim_to_world_projection_item(claim, kind=kind)
        for claim in answer.overrides
    )
    passed_refs: dict[tuple[int, int, int, str], QueryPlanEvidenceRef] = {}
    for claim in answer.items:
        if claim.gate_status != GateStatus.PASSED:
            continue
        for ref in claim.source_refs:
            queryplan_ref = _queryplan_evidence_ref(ref)
            passed_refs[
                (
                    ref.chapter_id,
                    ref.source_start,
                    ref.source_end,
                    ref.content_hash,
                )
            ] = queryplan_ref
    if passed_refs:
        status = "available"
    else:
        status = "candidate_only"
    return WorldProjectionOutcome(
        status=status,
        cutoff=int(context.through_chapter),
        refs=tuple(passed_refs.values()),
        items=items,
        overrides=overrides,
    )
