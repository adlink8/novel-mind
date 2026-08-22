"""Persistent three-space knowledge contract for v1.4 canon-fork boundaries.

D-35-01 / D-35-03: Original Canon, User Interpretation and Fanfiction Canon
exist only under explicit ``authority`` / ``namespace`` / ``version_key`` /
``citation_policy`` rules and freeze owner, novel, source snapshot/hash and
spoiler cutoff. The ``read_only`` marker is bound to the Original Canon space
(``(space = 'original_canon') = (read_only IS TRUE)``) so no mutable active row
ever replaces a version.

The chain is strict DTO -> ORM composite scope -> migration constraints
(``35_canon_space01.py``). Rows are append-only: only ``status`` may change on
an existing artifact; any lineage mutation or delete fails closed.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

CANON_SPACES = ("original_canon", "user_interpretation", "fanfiction_canon")
CANON_AUTHORITIES = ("source_text", "user_assertion", "creative_draft")
CANON_CITATION_POLICIES = (
    "original_leaf",
    "interpretation_with_original_refs",
    "fanfiction_only",
)
CANON_ARTIFACT_STATUSES = ("draft", "accepted", "rejected", "archived")
# Backfill sentinel for rows that predate the frozen lineage contract. New rows
# are always written with the real source snapshot hash by the contract layer.
_LEGACY_SNAPSHOT_SENTINEL = "0" * 64


class CanonSpaceArtifact(TimestampMixin, Base):
    """Owner/novel-scoped, versioned artifact in one knowledge space.

    This table is intentionally not consumed by raw chunk, unit, facet, or NM
    retrieval. Consumers must pass through the canon-fork contract layer first.
    """

    __tablename__ = "canon_space_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "space",
            "namespace",
            "version_key",
            name="uq_canon_space_artifact_version",
        ),
        CheckConstraint(
            "space IN ('original_canon','user_interpretation','fanfiction_canon')",
            name="ck_canon_space_artifact_space",
        ),
        CheckConstraint(
            "authority IN ('source_text','user_assertion','creative_draft')",
            name="ck_canon_space_artifact_authority",
        ),
        CheckConstraint(
            "citation_policy IN ('original_leaf','interpretation_with_original_refs','fanfiction_only')",
            name="ck_canon_space_artifact_citation",
        ),
        CheckConstraint(
            "status IN ('draft','accepted','rejected','archived')",
            name="ck_canon_space_artifact_status",
        ),
        # D-35-03 immutable lineage: the source snapshot the artifact is bound to.
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_canon_space_artifact_snapshot_hash",
        ),
        # D-35-03 spoiler cutoff: server-derived chapter bound, never 0.
        CheckConstraint(
            "through_chapter > 0",
            name="ck_canon_space_artifact_cutoff",
        ),
        # D-35-02 Original read-only marker: only original_canon may be read_only.
        CheckConstraint(
            "(space = 'original_canon') = (read_only = TRUE)",
            name="ck_canon_space_artifact_readonly",
        ),
        Index("ix_canon_space_artifacts_scope", "owner_id", "novel_id", "space"),
        Index(
            "ix_canon_space_artifacts_lineage",
            "space",
            "namespace",
            "version_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    space: Mapped[str] = mapped_column(String(32), nullable=False)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    version_key: Mapped[str] = mapped_column(String(128), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    citation_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    source_text_chunk_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSON, default=dict)
    # D-35-03 immutable lineage additions (35_canon_space01 migration).
    source_snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=_LEGACY_SNAPSHOT_SENTINEL
    )
    through_chapter: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    full_book_authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    read_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )


def _reject_artifact_lineage_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """Artifact lineage is immutable; only the status projection may change.

    D-35-03: a mutable active row must never replace a version. A new version is
    a new row; in-place edits to content, hashes, scope or the read_only marker
    fail closed (mirrors the Phase 34 append-only anchor contract).
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
            "status may change — create a new version instead of mutating "
            f"attributes: {sorted(forbidden)}"
        )


def _reject_artifact_delete(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(
        f"{type(target).__name__} records are immutable (append-only); "
        "a repair must create a new version"
    )


event.listen(CanonSpaceArtifact, "before_update", _reject_artifact_lineage_mutation)
event.listen(CanonSpaceArtifact, "before_delete", _reject_artifact_delete)
