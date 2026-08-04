"""Owner-scoped derivative editor project (Phase 36-01, D-36-01/D-36-03).

A Derivative Project is the root object of the Phase 36 derivative editor. It
exists **only** under an explicit Canon Fork (``fork_id`` FK, NOT NULL): per
D-36-01 the project is never inferred from the current reading page and the
client must always pick the fork explicitly. Per D-36-03 every write enters the
``fanfiction_canon`` namespace only; the ``space`` column is bound to
``'fanfiction_canon'`` at the database level so Original Canon / User
Interpretation can never become a project's write target.

The fork lineage (``fork_key``, ``source_version_key``,
``source_snapshot_hash``, ``through_chapter``, ``full_book_authorized``,
``cutoff_snapshot_hash``, ``scope_hash``, ``manifest_hash``) is frozen from the
chosen Canon Fork at creation and is immutable: an in-place mutation of the
lineage fields fails closed (mirrors the Phase 35 fork contract). Only
``name`` / ``description`` / ``status`` are mutable project state; the project
row itself is a normal CRUD row that the owner may delete or archive.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# D-36-03: the only namespace a derivative project may write into.
DERIVATIVE_PROJECT_SPACE = "fanfiction_canon"
DERIVATIVE_PROJECT_STATUSES = ("active", "archived")
# Fork statuses a project may bind to (a rejected/archived fork cannot anchor a
# project; candidate forks are acceptable draft anchors, D-36-01).
DERIVATIVE_PROJECT_USABLE_FORK_STATUSES = ("candidate", "approved")

# Lineage columns frozen from the chosen fork at creation.
_FROZEN_LINEAGE = frozenset(
    {
        "space",
        "fork_id",
        "fork_key",
        "source_version_key",
        "source_snapshot_hash",
        "through_chapter",
        "full_book_authorized",
        "cutoff_snapshot_hash",
        "scope_hash",
        "manifest_hash",
    }
)


class DerivativeProject(TimestampMixin, Base):
    """One owner-scoped editing project bound to an explicit Fanfiction Canon Fork."""

    __tablename__ = "derivative_projects"
    __table_args__ = (
        # Immutable identity: one project_key per owner/novel (like fork_key).
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "project_key",
            name="uq_derivative_projects_key",
        ),
        # D-36-03: Fanfiction Canon only — no Original/Interpretation write target.
        CheckConstraint(
            "space = 'fanfiction_canon'",
            name="ck_derivative_projects_space",
        ),
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_derivative_projects_status",
        ),
        # D-36-01 fork lineage: the frozen hashes are 64-hex and the server-derived
        # cutoff is never 0 (mirrors the canon_forks contract).
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_derivative_projects_snapshot_hash",
        ),
        CheckConstraint(
            "length(cutoff_snapshot_hash) = 64",
            name="ck_derivative_projects_cutoff_hash",
        ),
        CheckConstraint(
            "length(scope_hash) = 64",
            name="ck_derivative_projects_scope_hash",
        ),
        CheckConstraint(
            "length(manifest_hash) = 64",
            name="ck_derivative_projects_manifest_hash",
        ),
        CheckConstraint(
            "through_chapter > 0",
            name="ck_derivative_projects_cutoff",
        ),
        Index("ix_derivative_projects_scope", "owner_id", "novel_id"),
        Index("ix_derivative_projects_fork", "owner_id", "novel_id", "fork_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # D-36-01: a project cannot exist without its explicit Canon Fork.
    fork_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("canon_forks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )
    # ---- D-36-03 / D-36-01 frozen fork lineage (copied from the fork at create) ----
    space: Mapped[str] = mapped_column(
        String(32), nullable=False, default="fanfiction_canon"
    )
    fork_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    through_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    full_book_authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    cutoff_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)


def _reject_project_lineage_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """Frozen fork lineage is immutable; only name/description/status may change.

    D-36-01: a project is permanently anchored to its fork — any attempt to
    repoint the fork or edit the frozen version/cutoff/hash lineage fails closed;
    a new project must be created instead.
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.key not in {"created_at", "updated_at"} and attr.history.has_changes()
    }
    forbidden = changed & _FROZEN_LINEAGE
    if forbidden:
        raise ValueError(
            f"{type(target).__name__} fork lineage is immutable; only "
            "name/description/status may change — create a new project instead of "
            f"mutating: {sorted(forbidden)}"
        )


event.listen(DerivativeProject, "before_update", _reject_project_lineage_mutation)
