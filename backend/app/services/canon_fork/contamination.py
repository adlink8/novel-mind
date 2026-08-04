"""Shared derivative-write guard adapter and contamination phase gate (Phase 35-04).

REQ-FORK-01 / REQ-CRE-01 / REQ-CRE-02 / D-35-02: derivative content
(User Interpretation / Fanfiction Canon) must never enter the Original Canon
retrieval index, evaluation corpus or facet production chain.  This module is
the *single shared* derivative-write guard adapter wired into every Original
write entry point of the index / eval-corpus / facet chains.  Each guard fails
closed *inside the transaction*: a derivative space entering an Original
pipeline is rejected before any IO, the failed write is rolled back, and the
machine-readable ``blocked_reason`` is preserved — either on the raised
:class:`ContaminationBlockedError` (in-memory) and, when owner/novel are known,
durably in ``canon_contamination_blocks`` (audit).  A guard never deletes data
and never converts a rejection into an empty success (REQ-CRE-01 pitfall #4).

The phase gate (:class:`ContaminationPhaseGate`) turns those same boundaries
into a release verdict.  Its verdict vocabulary is *only* ``candidate`` /
``blocked``: without an executed upstream contract-availability preflight the
gate returns ``blocked``; an active pointer, an Original mutation, a
cross-owner leak, or an un-approved publication action each return ``blocked``
with the first auditable reason.  The gate is read-only — it never changes the
Phase 22 BLOCKED/0-of-3 ledger (D-35-04).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canon_fork import CanonFork
from app.models.canon_space import CanonSpaceArtifact
from app.services.canon_fork.contracts import (
    ORIGINAL_PIPELINES,
    CanonForkContractError,
    CanonScope,
    validate_scope,
)

ORIGINAL_CANON = "original_canon"
DERIVATIVE_SPACES = frozenset({"user_interpretation", "fanfiction_canon"})

# The three Original write chains this adapter guards (subset of ORIGINAL_PIPELINES).
INDEX_PIPELINE = "original_retrieval"
EVAL_PIPELINE = "evaluation"
FACET_PIPELINE = "facet"


class ContaminationBlockedReason(StrEnum):
    """Machine-readable fail-closed reasons shared by guards and the phase gate."""

    SPACE_UNKNOWN = "space_unknown"
    SPACE_EXCLUDED = "space_excluded"
    CROSS_OWNER = "owner_scope"
    CROSS_NOVEL = "novel_scope"
    MISSING_PREFLIGHT = "missing_contract_availability_preflight"
    ACTIVE_POINTER = "active_pointer"
    ORIGINAL_MUTATION = "original_mutation"
    CROSS_OWNER_LEAKAGE = "cross_owner_leakage"
    APPROVAL_REQUIRED = "approval_required"


class ContaminationBlockedError(ValueError):
    """A fail-closed rejection that carries its auditable blocked reason."""

    def __init__(
        self,
        blocked_reason: ContaminationBlockedReason,
        detail: str,
        *,
        pipeline: str,
        space: str,
        owner_id: int | None = None,
        novel_id: int | None = None,
        scope_hash: str | None = None,
    ) -> None:
        self.blocked_reason = blocked_reason
        self.detail = detail
        self.pipeline = pipeline
        self.space = space
        self.owner_id = owner_id
        self.novel_id = novel_id
        self.scope_hash = scope_hash
        super().__init__(f"{blocked_reason.value}: {detail}")


def _space_of(scope: CanonScope | None, space: str | None) -> str:
    """Resolve the effective space from an explicit value or a frozen scope."""
    if scope is not None:
        return scope.space.value if hasattr(scope.space, "value") else str(scope.space)
    if space is None or not str(space).strip():
        raise ContaminationBlockedError(
            ContaminationBlockedReason.SPACE_UNKNOWN,
            "no knowledge space supplied to the derivative-write guard",
            pipeline="unknown",
            space="",
        )
    return str(space)


class DerivativeWriteGuard:
    """Shared fail-closed adapter bound to exactly one Original pipeline.

    The same adapter is wired into every write entry of its chain; a guard
    instance exists for the Original index, the evaluation corpus and the facet
    production chain.  ``assert_write_allowed`` is a pure predicate (no DB);
    ``guard_write`` runs a write callback inside the caller's transaction and,
    on contamination, rolls the failed write back and preserves the reason.
    """

    def __init__(self, pipeline: str) -> None:
        if pipeline not in ORIGINAL_PIPELINES:
            raise CanonForkContractError(
                "unknown_pipeline",
                f"{pipeline} is not an Original consumer pipeline",
            )
        self.pipeline = pipeline

    # ------------------------------------------------------------------ pure
    def assert_write_allowed(
        self,
        *,
        space: str | None = None,
        owner_id: int | None = None,
        novel_id: int | None = None,
        scope: CanonScope | None = None,
    ) -> None:
        """Fail closed: a derivative space can never enter this Original chain.

        An explicit ``scope`` (when provided) is validated first and its space
        wins over ``space``; owner/novel are bound when both the scope and an
        explicit owner/novel are given.  Raises
        :class:`ContaminationBlockedError` — never returns an empty success.
        """
        if scope is not None:
            try:
                validate_scope(scope)
            except CanonForkContractError as exc:
                raise ContaminationBlockedError(
                    ContaminationBlockedReason.SPACE_UNKNOWN,
                    exc.detail,
                    pipeline=self.pipeline,
                    space=str(getattr(scope, "space", "")),
                    owner_id=owner_id,
                    novel_id=novel_id,
                    scope_hash=scope.scope_hash(),
                ) from exc
            if owner_id is not None and scope.owner_id != owner_id:
                raise ContaminationBlockedError(
                    ContaminationBlockedReason.CROSS_OWNER,
                    "write scope owner does not match the requested owner",
                    pipeline=self.pipeline,
                    space=_space_of(scope, space),
                    owner_id=owner_id,
                    novel_id=novel_id,
                    scope_hash=scope.scope_hash(),
                )
            if novel_id is not None and scope.novel_id != novel_id:
                raise ContaminationBlockedError(
                    ContaminationBlockedReason.CROSS_NOVEL,
                    "write scope novel does not match the requested novel",
                    pipeline=self.pipeline,
                    space=_space_of(scope, space),
                    owner_id=owner_id,
                    novel_id=novel_id,
                    scope_hash=scope.scope_hash(),
                )

        effective = _space_of(scope, space)
        if effective != ORIGINAL_CANON:
            raise ContaminationBlockedError(
                ContaminationBlockedReason.SPACE_EXCLUDED,
                f"{effective} cannot enter {self.pipeline}; original_canon is "
                "the only admissible write input for this Original chain",
                pipeline=self.pipeline,
                space=effective,
                owner_id=owner_id,
                novel_id=novel_id,
                scope_hash=scope.scope_hash() if scope is not None else None,
            )

    # ------------------------------------------------------------- transaction
    async def guard_write(
        self,
        db: AsyncSession,
        *,
        write: Callable[[AsyncSession], Awaitable[Any]],
        space: str | None = None,
        owner_id: int | None = None,
        novel_id: int | None = None,
        scope: CanonScope | None = None,
        attempt_hash: str | None = None,
    ) -> Any:
        """Run ``write(db)`` under this guard inside the caller's transaction.

        On contamination the guard rolls the failed write back, persists the
        auditable block record (when owner/novel are known) and re-raises the
        :class:`ContaminationBlockedError` with its ``blocked_reason`` intact.
        """
        try:
            self.assert_write_allowed(
                space=space, owner_id=owner_id, novel_id=novel_id, scope=scope
            )
            result = await write(db)
            await db.flush()
            return result
        except ContaminationBlockedError as exc:
            await db.rollback()
            if owner_id is not None and novel_id is not None:
                await record_contamination_block(
                    db,
                    exc,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    attempt_hash=attempt_hash,
                )
            raise


# Shared guard instances wired into the three Original write chains.
original_index_guard = DerivativeWriteGuard(INDEX_PIPELINE)
evaluation_corpus_guard = DerivativeWriteGuard(EVAL_PIPELINE)
facet_producer_guard = DerivativeWriteGuard(FACET_PIPELINE)


async def record_contamination_block(
    db: AsyncSession,
    error: ContaminationBlockedError,
    *,
    owner_id: int,
    novel_id: int,
    attempt_hash: str | None = None,
) -> None:
    """Persist one blocked attempt durably (audit) in its own transaction.

    Called *after* the failed transaction has rolled back, so the evidence
    survives even though the contaminated write never landed.  Duplicate
    identical attempts are idempotent via the composite unique scope
    ``(owner_id, novel_id, space, pipeline, attempt_hash)`` — the second
    insert is a no-op, never an error.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.canon_contamination import CanonContaminationBlock

    db.add(
        CanonContaminationBlock(
            owner_id=owner_id,
            novel_id=novel_id,
            space=error.space,
            pipeline=error.pipeline,
            blocked_reason=error.blocked_reason.value,
            detail=error.detail,
            scope_hash=error.scope_hash,
            attempt_hash=attempt_hash or "not_recorded",
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # Same (owner, novel, space, pipeline, attempt_hash) already recorded.
        await db.rollback()


# ---------------------------------------------------------------------------
# Contamination phase gate (release verdict: candidate | blocked only)
# ---------------------------------------------------------------------------


class PhaseGateVerdict(StrEnum):
    CANDIDATE = "candidate"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PhaseGateResult:
    verdict: PhaseGateVerdict
    blocked_reason: ContaminationBlockedReason | None = None
    detail: str | None = None


@dataclass(frozen=True)
class PhaseGateEvidence:
    """Read-only evidence the gate resolves before producing a verdict."""

    preflight_ok: bool = False
    active_pointer: bool = False
    original_mutated: bool = False
    cross_owner_leakage: bool = False
    publish_requested: bool = False
    approved: bool = False


def resolve_gate_verdict(evidence: PhaseGateEvidence) -> PhaseGateResult:
    """Deterministic verdict: only ``candidate`` or ``blocked``.

    The fixed chain is: upstream contract-availability preflight -> no active
    pointer -> no Original mutation -> no cross-owner leakage -> approval for a
    publish action.  Without an executed preflight the gate must return
    ``blocked``; no fake successful candidate is ever produced.
    """
    if not evidence.preflight_ok:
        return PhaseGateResult(
            PhaseGateVerdict.BLOCKED,
            ContaminationBlockedReason.MISSING_PREFLIGHT,
            "upstream contract-availability preflight was not executed; the "
            "contamination phase gate fails closed",
        )
    if evidence.active_pointer:
        return PhaseGateResult(
            PhaseGateVerdict.BLOCKED,
            ContaminationBlockedReason.ACTIVE_POINTER,
            "an active production pointer exists; a candidate-only release "
            "cannot be cut over",
        )
    if evidence.original_mutated:
        return PhaseGateResult(
            PhaseGateVerdict.BLOCKED,
            ContaminationBlockedReason.ORIGINAL_MUTATION,
            "Original Canon source snapshot does not replay; a mutation was "
            "detected and the release is blocked",
        )
    if evidence.cross_owner_leakage:
        return PhaseGateResult(
            PhaseGateVerdict.BLOCKED,
            ContaminationBlockedReason.CROSS_OWNER_LEAKAGE,
            "cross-owner rows resolve for the requested scope; the release is "
            "blocked",
        )
    if evidence.publish_requested and not evidence.approved:
        return PhaseGateResult(
            PhaseGateVerdict.BLOCKED,
            ContaminationBlockedReason.APPROVAL_REQUIRED,
            "publication requires explicit approval; no approval was supplied",
        )
    return PhaseGateResult(PhaseGateVerdict.CANDIDATE)


class ContaminationPhaseGate:
    """Read-only DB-backed release gate for the three knowledge spaces.

    It never writes, never moves an active pointer and never changes the
    Phase 22 BLOCKED/0-of-3 ledger (D-35-04).  All evidence is resolved from
    the caller-provided preflight flag plus read-only database queries.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _has_active_pointer(self, *, owner_id: int, novel_id: int) -> bool:
        count = await self._db.scalar(
            select(func.count())
            .select_from(CanonFork)
            .where(
                CanonFork.owner_id == owner_id,
                CanonFork.novel_id == novel_id,
                CanonFork.active.is_(True),
            )
        )
        return bool(count)

    async def _has_cross_owner_leakage(self, *, owner_id: int, novel_id: int) -> bool:
        for model in (CanonFork, CanonSpaceArtifact):
            count = await self._db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.novel_id == novel_id, model.owner_id != owner_id)
            )
            if count:
                return True
        return False

    async def _source_mutated(
        self, *, owner_id: int, novel_id: int, expected_snapshot_hash: str | None
    ) -> bool:
        if expected_snapshot_hash is None:
            return False
        from app.services.canon_fork.snapshot import CanonForkSnapshotService

        current_hash, _ = await CanonForkSnapshotService(self._db).load_source_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        return current_hash != expected_snapshot_hash

    async def evaluate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        preflight_ok: bool = False,
        publish_requested: bool = False,
        approved: bool = False,
        expected_snapshot_hash: str | None = None,
    ) -> PhaseGateResult:
        """Resolve read-only evidence and return a candidate/blocked verdict."""
        evidence = PhaseGateEvidence(
            preflight_ok=preflight_ok,
            active_pointer=await self._has_active_pointer(
                owner_id=owner_id, novel_id=novel_id
            ),
            original_mutated=await self._source_mutated(
                owner_id=owner_id,
                novel_id=novel_id,
                expected_snapshot_hash=expected_snapshot_hash,
            ),
            cross_owner_leakage=await self._has_cross_owner_leakage(
                owner_id=owner_id, novel_id=novel_id
            ),
            publish_requested=publish_requested,
            approved=approved,
        )
        return resolve_gate_verdict(evidence)


__all__ = [
    "DERIVATIVE_SPACES",
    "EVAL_PIPELINE",
    "FACET_PIPELINE",
    "INDEX_PIPELINE",
    "ORIGINAL_CANON",
    "CanonForkContractError",
    "ContaminationBlockedError",
    "ContaminationBlockedReason",
    "ContaminationPhaseGate",
    "DerivativeWriteGuard",
    "PhaseGateEvidence",
    "PhaseGateResult",
    "PhaseGateVerdict",
    "evaluation_corpus_guard",
    "facet_producer_guard",
    "original_index_guard",
    "record_contamination_block",
    "resolve_gate_verdict",
]
