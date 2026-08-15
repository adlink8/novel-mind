"""Append-only explicit divergence override for a derivative candidate (Phase 37-04).

D-37-03 / REQ-FORK-03 / REQ-CRE-06: a derivative candidate is only ever
persisted as ``candidate | blocked | needs_override`` and a divergence is only
ever accepted as an **explicit** ``CanonDelta`` override (kind / reason /
affected evidence / actor / time / approval / status). Nothing here writes
Original Canon / User Interpretation / Narrative Memory and nothing promotes a
candidate to an active pointer (D-37-02 forbidden publish path).

The row freezes the override surface:

- ``kind`` — the closed divergence class (``character``, ``timeline``,
  ``world_rule``, ``clue``, ``other``), matching ``DIVERGENCE_TYPES``;
- ``reason`` — the owner-stated, non-empty divergence reason;
- ``affected_evidence`` — the evidence keys the divergence deliberately
  departs from (validated against the sealed package allowlist);
- ``canon_delta_hash`` — the byte-replayable CanonDelta lineage (the
  candidate's frozen ``canon_delta_hash`` when the candidate declared a
  CanonDelta, else the deterministic hash of this override's own delta);
- ``evidence_snapshot`` — the frozen gate/candidate audit (gate verdict,
  reason, divergence, citation keys) so the approval decision is always
  reviewable against the exact evidence that produced the blocked/override
  verdict;
- ``actor_id`` / ``created_at`` — who requested the override and when;
- ``approval_state`` (``pending | approved | rejected``) + ``approver_id`` /
  ``approved_at`` / ``rejected_at`` / ``approval_reason`` — the explicit
  owner review action journal.

Approval state transitions are the **only** mutable columns: ``approval_state``
may move ``pending -> approved | rejected`` once, and the approver/time/reason
columns are written at the same transition. The frozen surface (kind, reason,
affected evidence, canon delta hash, evidence snapshot, candidate/job/project/
chapter/fork lineage) fails closed on any in-place mutation or delete, so a
decided override can never be silently rewritten (T-37-04-01 repudiation gate).
A candidate maps to exactly one override row (``uq_derivative_overrides_candidate``),
so a candidate can never be approved twice through different override rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

# Closed divergence classes (matches candidate.DIVERGENCE_TYPES, D-37-03).
DERIVATIVE_OVERRIDE_KINDS = ("character", "timeline", "world_rule", "clue", "other")
# Explicit review action lifecycle (a decided override is terminal).
DERIVATIVE_OVERRIDE_STATUSES = ("pending", "approved", "rejected")

# Frozen override surface: mutations of these fields fail closed (T-37-04-01).
_FROZEN_OVERRIDE_FIELDS = frozenset(
    {
        "owner_id",
        "novel_id",
        "project_id",
        "chapter_id",
        "fork_id",
        "candidate_id",
        "job_id",
        "kind",
        "reason",
        "affected_evidence",
        "canon_delta_hash",
        "evidence_snapshot",
        "actor_id",
    }
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class DerivativeOverride(TimestampMixin, Base):
    """One explicit owner divergence override bound to a blocked/override candidate."""

    __tablename__ = "derivative_overrides"
    __table_args__ = (
        # A candidate can only ever be overridden once (no double approval).
        UniqueConstraint(
            "candidate_id",
            name="uq_derivative_overrides_candidate",
        ),
        CheckConstraint(
            f"kind IN ({_sql_values(DERIVATIVE_OVERRIDE_KINDS)})",
            name="ck_derivative_overrides_kind",
        ),
        CheckConstraint(
            f"approval_state IN ({_sql_values(DERIVATIVE_OVERRIDE_STATUSES)})",
            name="ck_derivative_overrides_approval",
        ),
        CheckConstraint(
            "length(canon_delta_hash) = 64",
            name="ck_derivative_overrides_delta_hash",
        ),
        CheckConstraint(
            "reason <> ''",
            name="ck_derivative_overrides_reason",
        ),
        Index(
            "ix_derivative_overrides_scope",
            "owner_id",
            "novel_id",
            "project_id",
            "candidate_id",
        ),
        Index(
            "ix_derivative_overrides_status",
            "owner_id",
            "novel_id",
            "approval_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    # The target derivative project/chapter the approved override materializes
    # into. Both are owner/novel scoped at write time (service gate).
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The fork scope the candidate/package belong to (Fanfiction Canon only).
    fork_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("canon_forks.id", ondelete="CASCADE"), nullable=False
    )
    # The immutable candidate being overridden + its generation job lineage.
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_generation_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # D-37-03: closed divergence class (only an explicit CanonDelta is accepted).
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Owner-stated divergence reason (non-empty, auditable).
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Evidence keys the divergence deliberately departs from (sealed allowlist).
    affected_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Byte-replayable CanonDelta lineage (candidate's delta hash or override hash).
    canon_delta_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Frozen gate/candidate audit so approval decisions stay reviewable.
    evidence_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Who requested the override and when (auditable actor/time).
    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # ---- explicit review action journal (approval/status, T-37-04-01) ----
    approval_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    approver_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The owner's explicit approval/rejection note (approval required).
    approval_reason: Mapped[str | None] = mapped_column(Text)


def _reject_override_frozen_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """The frozen override surface is immutable; only the review action may change.

    ``approval_state`` is allowed to transition ``pending -> approved | rejected``
    once, together with the approver/time/reason journal columns. Any attempt to
    rewrite the divergence surface (kind/reason/evidence/delta/candidate linkage)
    fails closed so a decided override can never be silently tampered with.
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.key not in {"created_at", "updated_at"} and attr.history.has_changes()
    }
    forbidden = changed & _FROZEN_OVERRIDE_FIELDS
    if forbidden:
        raise ValueError(
            f"{type(target).__name__} frozen override surface is immutable; "
            "only the approval action may change — create a new override instead "
            f"of mutating: {sorted(forbidden)}"
        )
    allowed = {
        "approval_state",
        "approver_id",
        "approved_at",
        "rejected_at",
        "approval_reason",
    }
    unexpected = changed - allowed
    if unexpected:
        raise ValueError(
            f"{type(target).__name__} has unexpected mutable fields: "
            f"{sorted(unexpected)}"
        )


def _reject_override_delete(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(
        f"{type(target).__name__} records are append-only audit rows; "
        "overrides cannot be deleted"
    )


event.listen(DerivativeOverride, "before_update", _reject_override_frozen_mutation)
event.listen(DerivativeOverride, "before_delete", _reject_override_delete)

__all__ = [
    "DERIVATIVE_OVERRIDE_KINDS",
    "DERIVATIVE_OVERRIDE_STATUSES",
    "DerivativeOverride",
]
