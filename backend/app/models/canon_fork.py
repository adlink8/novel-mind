"""Immutable owner/novel-scoped Canon Fork candidates (Phase 35-02, D-35-03).

A Canon Fork freezes ``owner``, ``novel``, the Original Canon ``version`` it
derives from, the server-derived spoiler ``cutoff``, the source snapshot/hash
and the citation lineage. Per the Agent Consumer Contract the fork is a
``CanonForkProposal``: mutation only ever creates a candidate row and never
creates or switches a production ``active`` pointer — ``active`` is bound to
``FALSE`` at the database level.

The row is append-only: only ``status`` may change on an existing fork; any
lineage mutation or delete fails closed (mirrors the Phase 34 anchor and
``canon_space_artifacts`` contracts).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    event,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

CANON_FORK_SPACE = "fanfiction_canon"
CANON_FORK_STATUSES = ("candidate", "approved", "rejected", "archived")


class CanonFork(TimestampMixin, Base):
    """One immutable candidate fork with frozen owner/version/cutoff/snapshot lineage."""

    __tablename__ = "canon_forks"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "fork_key",
            name="uq_canon_forks_key",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "manifest_hash",
            name="uq_canon_forks_manifest",
        ),
        # D-35-01: forks are Fanfiction Canon derivatives; a fork in any other
        # space is not a Canon Fork and must fail closed.
        CheckConstraint(
            "space = 'fanfiction_canon'",
            name="ck_canon_forks_space",
        ),
        CheckConstraint(
            "status IN ('candidate','approved','rejected','archived')",
            name="ck_canon_forks_status",
        ),
        # D-35-03 frozen lineage: the source snapshot/hash and the sealed
        # manifest must be 64-hex and the cutoff must be server-derived (never 0).
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_canon_forks_snapshot_hash",
        ),
        CheckConstraint(
            "length(cutoff_snapshot_hash) = 64",
            name="ck_canon_forks_cutoff_hash",
        ),
        CheckConstraint(
            "length(scope_hash) = 64",
            name="ck_canon_forks_scope_hash",
        ),
        CheckConstraint(
            "length(manifest_hash) = 64",
            name="ck_canon_forks_manifest_hash",
        ),
        CheckConstraint("through_chapter > 0", name="ck_canon_forks_cutoff"),
        # D-35-03: no production active pointer is ever created for a fork;
        # the marker is bound to FALSE for every row.
        CheckConstraint(
            "active = false",
            name="ck_canon_forks_no_active_pointer",
        ),
        Index("ix_canon_forks_scope", "owner_id", "novel_id"),
        Index(
            "ix_canon_forks_lineage",
            "space",
            "source_version_key",
            "source_snapshot_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fork_key: Mapped[str] = mapped_column(String(128), nullable=False)
    space: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate"
    )
    # Frozen Original Canon version the fork derives from (server-derived).
    source_version_key: Mapped[str] = mapped_column(String(128), nullable=False)
    # Deterministic source snapshot identity the fork is sealed to.
    source_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Server-derived spoiler cutoff and its frozen boundary hash.
    through_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    full_book_authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    cutoff_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Frozen citation lineage (source leaf provenance the fork is bound to).
    citation_lineage: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Auditable scope record: how the cutoff was authorized on the server.
    authorization: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Candidate-only marker; always FALSE (no production active pointer).
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )


def _reject_fork_lineage_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """Fork lineage is immutable; only the status projection may change.

    D-35-03: a mutable active row must never replace a fork version. In-place
    edits to scope, hashes, lineage or the ``active`` marker fail closed; a new
    fork version is a new row.
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.key not in {"created_at", "updated_at"} and attr.history.has_changes()
    }
    allowed = {"status"}
    forbidden = changed - allowed
    if forbidden:
        raise ValueError(
            f"{type(target).__name__} lineage is immutable (append-only); only "
            "status may change — create a new fork version instead of mutating "
            f"attributes: {sorted(forbidden)}"
        )


def _reject_fork_delete(_mapper: object, _connection: object, target: object) -> None:
    raise ValueError(
        f"{type(target).__name__} records are immutable (append-only); "
        "a repair must create a new fork version"
    )


event.listen(CanonFork, "before_update", _reject_fork_lineage_mutation)
event.listen(CanonFork, "before_delete", _reject_fork_delete)
