"""Hash-verified illustration anchor contract (Phase 34-01, REQ-VIS-05).

D-34-01 / D-34-03: an approved illustration stays consistent between the
reader and every export through a hash-verified anchor bound to
owner/novel/chapter, an immutable source snapshot, exact source coordinates
and the proposal-ready AssetRevision. This module is the anchor/version plane
(the ``reader_chat.py`` selection/evidence and ``illustration.py`` analog):

- ``illustration_anchor_proposals``: append-only candidate proposals. A
  proposal carries the exact source span (excerpt + anchor hash), the frozen
  chapter content hash / source snapshot and the proposal-ready AssetRevision
  ref. The full lifecycle status distinguishes ``proposed`` /
  ``pending_approval`` / ``valid`` / ``needs_repair`` / ``invalid``; only the
  Phase 34 approval/publisher transaction (34-05) may fill
  ``published_asset_revision_id`` + ``publish_manifest_hash`` and enter
  ``valid``. Text/version drift marks ``needs_repair`` or ``invalid`` and never
  silently relocates to a nearby paragraph (D-34-01/03).
- ``illustration_anchors``: the published, reader/export-visible anchor. It is
  created only by the deterministic publish transaction and must bind an
  approved action (``approval_request_id``), the published AssetRevision and a
  frozen publish manifest hash. Stale/missing presentation is explicit
  (``needs_repair`` / ``invalid``), never a broken URL or silent drop
  (D-34-02/04).

Design conventions (following ``illustration.py`` / ``visual_bible.py``):
- Proposals are append-only — SQLAlchemy events reject UPDATE/DELETE so no
  silent coordinate/hash/status mutation is possible; the anchor row's status
  is the single mutable projection on the published side.
- Every row stores its canonical payload JSON, a SHA-256 canonical payload hash
  and a unique idempotency key; re-append only replays the existing row.
- No ``cover_url`` / DOM index / nearest-match relocation: anchors are exact
  source spans and a hash mismatch is stale, never relocated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

# D-34-01/03 lifecycle statuses. ``proposed`` / ``pending_approval`` live only
# on a proposal row; ``valid`` / ``needs_repair`` / ``invalid`` are the
# published anchor statuses (and the post-publish proposal statuses).
ILLUSTRATION_ANCHOR_STATUSES = (
    "proposed",
    "pending_approval",
    "valid",
    "needs_repair",
    "invalid",
)
# The only statuses a published anchor row may carry.
ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES = ("valid", "needs_repair", "invalid")


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


@dataclass(frozen=True)
class AnchorRange:
    """Exact source span (code-point offsets) plus optional paragraph range.

    ``source_start`` / ``source_end`` are the immutable span the anchor hash is
    verified against; ``paragraph_start`` / ``paragraph_end`` are optional
    paragraph-range coordinates for the reader/export layout. Offset/hash
    mismatch makes an anchor stale — it must never move to a nearby paragraph.
    """

    source_start: int
    source_end: int
    paragraph_start: int | None = None
    paragraph_end: int | None = None


class IllustrationAnchorProposal(TimestampMixin, Base):
    """Append-only hash-verified anchor proposal (D-34-01).

    The proposal is created ``proposed`` from a proposal-ready AssetRevision
    and an exact source span; attaching the Web approval request moves it to
    ``pending_approval``; the deterministic publish transaction fills
    ``published_asset_revision_id`` + ``publish_manifest_hash`` and moves it to
    ``valid`` (34-05 owns publish; nothing here becomes reader/export visible).
    """

    __tablename__ = "illustration_anchor_proposals"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_illustration_anchor_proposals_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "proposal_key",
            name="uq_illustration_anchor_proposals_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_illustration_anchor_proposals_idempotency",
        ),
        Index(
            "idx_illustration_anchor_proposals_scope",
            "owner_id",
            "novel_id",
            "status",
        ),
        Index(
            "idx_illustration_anchor_proposals_chapter",
            "owner_id",
            "novel_id",
            "chapter_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "proposal_asset_revision_id"],
            [
                "asset_revisions.owner_id",
                "asset_revisions.novel_id",
                "asset_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_illustration_anchor_proposals_asset_scope",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(ILLUSTRATION_ANCHOR_STATUSES)})",
            name="ck_illustration_anchor_proposals_status",
        ),
        CheckConstraint(
            # Only the 34-05 deterministic publish transaction may fill the
            # published asset/manifest and enter ``valid`` (D-34-01).
            "(status = 'valid' AND published_asset_revision_id IS NOT NULL "
            "AND publish_manifest_hash IS NOT NULL) OR status <> 'valid'",
            name="ck_illustration_anchor_proposals_publish_shape",
        ),
        CheckConstraint(
            # An approval request only attaches after ``proposed``.
            "(approval_request_id IS NULL) OR status IN "
            "('pending_approval','valid','needs_repair','invalid')",
            name="ck_illustration_anchor_proposals_approval_shape",
        ),
        CheckConstraint(
            "chapter_number >= 1",
            name="ck_illustration_anchor_proposals_chapter_number",
        ),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_illustration_anchor_proposals_offsets",
        ),
        CheckConstraint(
            "(paragraph_start IS NULL AND paragraph_end IS NULL) OR "
            "(paragraph_start >= 1 AND paragraph_end >= paragraph_start)",
            name="ck_illustration_anchor_proposals_paragraph",
        ),
        CheckConstraint(
            "length(anchor_hash) = 64",
            name="ck_illustration_anchor_proposals_anchor_hash",
        ),
        CheckConstraint(
            "length(chapter_content_hash) = 64",
            name="ck_illustration_anchor_proposals_content_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_illustration_anchor_proposals_snapshot_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_illustration_anchor_proposals_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_illustration_anchor_proposals_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    proposal_key: Mapped[str] = mapped_column(String(160), nullable=False)
    # Immutable source snapshot the anchor is bound to (D-34-01).
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Exact source span coordinates (code-point offsets) and optional
    # paragraph-range coordinates for reader/export layout.
    paragraph_start: Mapped[int | None] = mapped_column(Integer)
    paragraph_end: Mapped[int | None] = mapped_column(Integer)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    # D-34-01: hash of the exact excerpt; any mismatch is stale, never
    # relocated to a nearby paragraph.
    anchor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Proposal-ready AssetRevision ref (Phase 33 handoff, never auto-created).
    proposal_asset_revision_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Web approval request (25.3): proposal -> pending_approval.
    approval_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("approval_requests.id", ondelete="SET NULL")
    )
    # 34-05 deterministic publish fills these and enters ``valid``.
    published_asset_revision_id: Mapped[int | None] = mapped_column(Integer)
    publish_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="proposed",
        server_default="proposed",
    )
    # Accessible caption/alt/citation contract (D-34-02).
    caption: Mapped[str] = mapped_column(String(500), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(500), nullable=False)
    citation: Mapped[str] = mapped_column(String(1000), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class IllustrationAnchor(TimestampMixin, Base):
    """Published reader/export-visible anchor (D-34-01/02/04).

    Created only by the 34-05 deterministic publish transaction from an
    approved proposal; it must bind an approved action, the published
    AssetRevision and a frozen publish manifest hash. ``valid`` /
    ``needs_repair`` / ``invalid`` are the only statuses; a missing binary or a
    drifted source is presented explicitly, never as a broken URL or silent
    drop.
    """

    __tablename__ = "illustration_anchors"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_illustration_anchors_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "anchor_key",
            name="uq_illustration_anchors_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_illustration_anchors_idempotency",
        ),
        Index(
            "idx_illustration_anchors_scope",
            "owner_id",
            "novel_id",
            "status",
        ),
        Index(
            "idx_illustration_anchors_chapter",
            "owner_id",
            "novel_id",
            "chapter_id",
        ),
        Index("idx_illustration_anchors_proposal", "proposal_id"),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "published_asset_revision_id"],
            [
                "asset_revisions.owner_id",
                "asset_revisions.novel_id",
                "asset_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_illustration_anchors_published_asset_scope",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES)})",
            name="ck_illustration_anchors_status",
        ),
        CheckConstraint(
            # A valid published anchor must bind the approved action, the
            # published asset and the publish manifest (D-34-01).
            "(status = 'valid' AND published_asset_revision_id IS NOT NULL "
            "AND publish_manifest_hash IS NOT NULL AND approval_request_id IS NOT NULL) "
            "OR status IN ('needs_repair','invalid')",
            name="ck_illustration_anchors_publish_shape",
        ),
        CheckConstraint(
            "chapter_number >= 1",
            name="ck_illustration_anchors_chapter_number",
        ),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_illustration_anchors_offsets",
        ),
        CheckConstraint(
            "(paragraph_start IS NULL AND paragraph_end IS NULL) OR "
            "(paragraph_start >= 1 AND paragraph_end >= paragraph_start)",
            name="ck_illustration_anchors_paragraph",
        ),
        CheckConstraint(
            "length(anchor_hash) = 64",
            name="ck_illustration_anchors_anchor_hash",
        ),
        CheckConstraint(
            "length(chapter_content_hash) = 64",
            name="ck_illustration_anchors_content_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_illustration_anchors_snapshot_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_illustration_anchors_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_illustration_anchors_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    anchor_key: Mapped[str] = mapped_column(String(160), nullable=False)
    proposal_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("illustration_anchor_proposals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    paragraph_start: Mapped[int | None] = mapped_column(Integer)
    paragraph_end: Mapped[int | None] = mapped_column(Integer)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Published AssetRevision + frozen publish manifest (34-05 owns both).
    published_asset_revision_id: Mapped[int] = mapped_column(Integer, nullable=False)
    publish_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("approval_requests.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="valid",
        server_default="valid",
    )
    caption: Mapped[str] = mapped_column(String(500), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(500), nullable=False)
    citation: Mapped[str] = mapped_column(String(1000), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


def _reject_proposal_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """Proposal content is immutable; only the approval/publish projection moves.

    ``status`` / ``approval_request_id`` / ``published_asset_revision_id`` /
    ``publish_manifest_hash`` / ``approved_by`` / ``approved_at`` mirror the
    AssetRevision ``approval_state`` projection pattern: the append-only review
    events and the immutable span/hash/asset ref stay frozen, while the 34-05
    deterministic publish transaction may move a proposal to ``valid``. A
    repair must propose a new candidate anchor (D-34-03); everything else fails
    closed on any in-place change.
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.key not in {"created_at", "updated_at"} and attr.history.has_changes()
    }
    allowed = {
        "status",
        "approval_request_id",
        "published_asset_revision_id",
        "publish_manifest_hash",
        "approved_by",
        "approved_at",
    }
    forbidden = changed - allowed
    if forbidden:
        raise ValueError(
            "IllustrationAnchorProposal content is immutable; a repair must "
            "propose a new anchor; unexpected changes: "
            f"{sorted(forbidden)}"
        )


def _reject_anchor_content_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """Published anchor content is immutable; only the status projection moves.

    ``status`` is the single mutable projection on a published anchor so a
    stale/missing anchor can be surfaced explicitly (``needs_repair`` /
    ``invalid``, D-34-03). Everything else (coordinates, hashes, asset ref,
    manifest) fails closed on any in-place change.
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
            "IllustrationAnchor content is immutable; unexpected changes: "
            f"{sorted(forbidden)}"
        )


def _reject_content_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(
        f"{type(target).__name__} records are immutable (append-only); a repair "
        "must propose a new anchor (D-34-03)"
    )


# Proposals are append-only (no edit, no delete); a published anchor keeps only
# its status projection mutable for explicit stale/repair presentation.
event.listen(IllustrationAnchorProposal, "before_update", _reject_proposal_mutation)
event.listen(IllustrationAnchorProposal, "before_delete", _reject_content_mutation)
event.listen(IllustrationAnchor, "before_update", _reject_anchor_content_mutation)
event.listen(IllustrationAnchor, "before_delete", _reject_content_mutation)
