"""Append-only sealed derivative context package record (Phase 37-01, D-37-01).

A ``ContextPackageRecord`` is the immutable, auditable, replayable artifact a
derivative generator consumes (REQ-FORK-03 / REQ-CRE-05). The row freezes the
server-derived cutoff lineage of one Canon Fork plus the compiled dimensions
(world state, timeline causality, unresolved clues, world rules, leaf evidence
refs and user intent) into a canonical JSON payload sealed with a SHA-256
``package_hash``.

Contract (mirrors the Phase 35/36 append-only lineage conventions):

- **Fork-bound:** ``fork_id`` is a NOT NULL FK to ``canon_forks``; a package
  cannot exist without its explicit Canon Fork.
- **Fanfiction-only:** ``space`` is bound to ``'fanfiction_canon'`` at the
  database level so no Original Canon / User Interpretation write target can
  ever be created.
- **Server-derived lineage:** ``fork_key``, ``source_version_key``,
  ``source_snapshot_hash``, ``through_chapter``, ``full_book_authorized``,
  ``cutoff_snapshot_hash``, ``scope_hash`` and ``manifest_hash`` are copied
  from the chosen fork and sealed with 64-hex check constraints.
- **Immutable append-only:** any in-place mutation or delete fails closed; a
  re-compile with identical input replays the same row, a changed scope is a
  new row (or a conflict).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

# D-37-01: generation packages are Fanfiction Canon context only.
DERIVATIVE_CONTEXT_SPACE = "fanfiction_canon"
DERIVATIVE_CONTEXT_INTENTS = ("continuation", "rewrite")

# Lineage columns frozen from the chosen fork at compile time (immutable).
_FROZEN_PACKAGE_LINEAGE = frozenset(
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
        "intent",
        "canonical_payload",
        "package_hash",
        "budget_estimate",
    }
)


class ContextPackageRecord(TimestampMixin, Base):
    """One immutable sealed context package bound to a Fanfiction Canon Fork."""

    __tablename__ = "derivative_context_packages"
    __table_args__ = (
        # Immutable identity: one package per fork/intent/cutoff per owner/novel.
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "package_key",
            name="uq_derivative_context_packages_key",
        ),
        # D-37-01: Fanfiction Canon only — no Original/Interpretation target.
        CheckConstraint(
            "space = 'fanfiction_canon'",
            name="ck_derivative_context_packages_space",
        ),
        CheckConstraint(
            "intent IN ('continuation','rewrite')",
            name="ck_derivative_context_packages_intent",
        ),
        # Server-derived fork lineage is sealed at compile time.
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_derivative_context_packages_snapshot_hash",
        ),
        CheckConstraint(
            "length(cutoff_snapshot_hash) = 64",
            name="ck_derivative_context_packages_cutoff_hash",
        ),
        CheckConstraint(
            "length(scope_hash) = 64",
            name="ck_derivative_context_packages_scope_hash",
        ),
        CheckConstraint(
            "length(manifest_hash) = 64",
            name="ck_derivative_context_packages_manifest_hash",
        ),
        CheckConstraint(
            "length(package_hash) = 64",
            name="ck_derivative_context_packages_package_hash",
        ),
        CheckConstraint(
            "through_chapter > 0",
            name="ck_derivative_context_packages_cutoff",
        ),
        Index("ix_derivative_context_packages_scope", "owner_id", "novel_id"),
        Index(
            "ix_derivative_context_packages_fork",
            "owner_id",
            "novel_id",
            "fork_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # D-37-01: a package cannot exist without its explicit Canon Fork.
    fork_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("canon_forks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_key: Mapped[str] = mapped_column(String(128), nullable=False)
    # D-37-01: Fanfiction Canon only.
    space: Mapped[str] = mapped_column(
        String(32), nullable=False, default="fanfiction_canon"
    )
    intent: Mapped[str] = mapped_column(String(16), nullable=False)
    # ---- frozen fork lineage (copied from the fork at compile) ----
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
    # ---- sealed payload ----
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    budget_estimate: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)


def _reject_package_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """Packages are immutable: any in-place mutation fails closed.

    A changed scope must produce a new row (or a deterministic conflict); no
    process can silently rewrite the sealed audit record.
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.key not in {"created_at", "updated_at"} and attr.history.has_changes()
    }
    if changed:
        raise ValueError(
            f"{type(target).__name__} rows are immutable (append-only); compile a "
            f"new package instead of mutating: {sorted(changed)}"
        )


def _reject_package_delete(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(
        f"{type(target).__name__} records are immutable (append-only); "
        "audit records cannot be deleted"
    )


event.listen(ContextPackageRecord, "before_update", _reject_package_mutation)
event.listen(ContextPackageRecord, "before_delete", _reject_package_delete)

__all__ = [
    "DERIVATIVE_CONTEXT_INTENTS",
    "DERIVATIVE_CONTEXT_SPACE",
    "ContextPackageRecord",
]
