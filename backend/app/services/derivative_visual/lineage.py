"""Phase 38-01 derivative Visual Bible lineage (review + owner-scoped reads).

Mirrors the Phase 30-04 visual review seam but for the **derivative**
namespace: an approval here only moves the derivative candidate's
``review_state`` and never touches the Original Visual Bible rows or their
assets (REQ-FORK-04). This module owns:

- ``apply_review`` — append-only, explicit, idempotent review actions
  (approve/reject/edit/supersede/needs_relink) whose legality is decided
  server-side from the derivative version's current review state;
- ``list_versions`` / ``load_version_view`` — owner-scoped read seams that
  expose the source snapshot ref, divergence, provenance, assets and review
  events without leaking any row outside the owner/novel boundary and without
  ever exposing an Original Visual Bible row as a derivative write target.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_visual import (
    DerivativeVisualAsset,
    DerivativeVisualEntity,
    DerivativeVisualReviewEvent,
    DerivativeVisualVersion,
)
from app.schemas.derivative_visual import (
    DerivativeVisualReviewEventInput,
    DerivativeVisualReviewEventView,
    DerivativeVisualState,
    DerivativeVisualVersionView,
    validate_derivative_visual_review_event,
)


class DerivativeVisualLineageError(ValueError):
    """Fail-closed derivative visual lineage gate violation."""


class DerivativeVisualVersionNotFoundError(DerivativeVisualLineageError):
    pass


class DerivativeVisualScopeMismatchError(DerivativeVisualLineageError):
    pass


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeVisualScopeMismatchError(
            "scope identifiers must be explicit positive integers"
        )


async def apply_review(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    event: DerivativeVisualReviewEventInput,
    details: dict | None = None,
) -> DerivativeVisualVersion:
    """Append an explicit, idempotent review action to a derivative version.

    A repeated ``event_key`` (retried approval) only replays the existing
    state; it never appends a second event and never creates a second
    approval. Approval moves only the derivative ``review_state`` projection —
    the Original Visual Bible rows and their assets are never touched.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if event.owner_id != owner_id or event.novel_id != novel_id:
        raise DerivativeVisualScopeMismatchError(
            "review event scope does not match request scope"
        )

    version = await _version(
        db, owner_id=owner_id, novel_id=novel_id, version_id=event.version_id
    )
    if version is None:
        raise DerivativeVisualVersionNotFoundError(
            "derivative visual version not found in explicit owner/novel scope"
        )

    existing = await db.scalar(
        select(DerivativeVisualReviewEvent).where(
            DerivativeVisualReviewEvent.owner_id == owner_id,
            DerivativeVisualReviewEvent.novel_id == novel_id,
            DerivativeVisualReviewEvent.version_id == version.id,
            DerivativeVisualReviewEvent.event_key == event.event_key,
        )
    )
    if existing is not None:
        return version  # idempotent replay: no second event, no state change

    if version.review_state != event.from_review_state.value:
        raise DerivativeVisualLineageError(
            f"review from_review_state {event.from_review_state.value!r} does not "
            f"match the version's current state {version.review_state!r}"
        )

    to_state = validate_derivative_visual_review_event(event)

    review_row = DerivativeVisualReviewEvent(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version.id,
        action=event.action.value,
        actor_source=event.actor_source.value,
        actor=event.actor,
        reason=event.reason,
        event_key=event.event_key,
        from_review_state=event.from_review_state.value,
        to_review_state=to_state.value,
        details=details or {},
    )
    db.add(review_row)
    version.review_state = to_state.value
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent duplicate event_key: roll back and replay the winner.
        await db.rollback()
        version = await _version(
            db, owner_id=owner_id, novel_id=novel_id, version_id=event.version_id
        )
        if version is None:
            raise DerivativeVisualVersionNotFoundError(
                "derivative visual version disappeared during review replay"
            )
        return version
    return version


# ---------------------------------------------------------------------------
# Owner-scoped read seams (candidate-only, no unauthorized exposure)
# ---------------------------------------------------------------------------


async def list_versions(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
) -> list[DerivativeVisualVersionView]:
    """List every derivative visual version for the owned novel, oldest first."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    rows = (
        await db.scalars(
            select(DerivativeVisualVersion)
            .where(
                DerivativeVisualVersion.owner_id == owner_id,
                DerivativeVisualVersion.novel_id == novel_id,
            )
            .order_by(DerivativeVisualVersion.id.asc())
        )
    ).all()
    return [
        await load_version_view(
            db, owner_id=owner_id, novel_id=novel_id, version_id=row.id
        )
        for row in rows
    ]


async def load_version_view(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> DerivativeVisualVersionView:
    """Full derivative envelope for one owned version; raises on scope."""
    version = await _version(
        db, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    if version is None:
        raise DerivativeVisualVersionNotFoundError(
            "derivative visual version not found in explicit owner/novel scope"
        )

    entity_rows = (
        await db.scalars(
            select(DerivativeVisualEntity)
            .where(
                DerivativeVisualEntity.owner_id == owner_id,
                DerivativeVisualEntity.novel_id == novel_id,
                DerivativeVisualEntity.version_id == version_id,
            )
            .order_by(DerivativeVisualEntity.id.asc())
        )
    ).all()
    asset_rows = (
        await db.scalars(
            select(DerivativeVisualAsset)
            .where(
                DerivativeVisualAsset.owner_id == owner_id,
                DerivativeVisualAsset.novel_id == novel_id,
                DerivativeVisualAsset.version_id == version_id,
            )
            .order_by(DerivativeVisualAsset.id.asc())
        )
    ).all()
    review_rows = (
        await db.scalars(
            select(DerivativeVisualReviewEvent)
            .where(
                DerivativeVisualReviewEvent.owner_id == owner_id,
                DerivativeVisualReviewEvent.novel_id == novel_id,
                DerivativeVisualReviewEvent.version_id == version_id,
            )
            .order_by(DerivativeVisualReviewEvent.id.asc())
        )
    ).all()

    return DerivativeVisualVersionView(
        id=version.id,
        owner_id=version.owner_id,
        novel_id=version.novel_id,
        project_id=version.project_id,
        fork_id=version.fork_id,
        namespace=version.visual_namespace,
        version_key=version.version_key,
        revision_number=version.revision_number,
        parent_version_id=version.parent_version_id,
        source_version_id=version.source_version_id,
        source_snapshot_id=version.source_snapshot_id,
        source_snapshot_hash=version.source_snapshot_hash,
        source_manifest_hash=version.source_manifest_hash,
        cutoff_chapter=version.cutoff_chapter,
        divergence=version.divergence,
        provenance=version.provenance,
        schema_version=version.schema_version,
        schema_hash=version.schema_hash,
        policy_hash=version.policy_hash,
        manifest_hash=version.manifest_hash,
        review_state=DerivativeVisualState(version.review_state),
        style_profile=version.style_profile,
        constraints=version.constraints,
        entities=[
            {
                "stable_id": row.stable_id,
                "entity_key": row.entity_key,
                "entity_type": row.entity_type,
                "description": row.description,
                "authority": row.authority,
                "divergence": row.divergence,
                "source_entity_ref": row.source_entity_ref,
                "disclosure_cutoff": row.disclosure_cutoff,
            }
            for row in entity_rows
        ],
        reference_assets=[
            {
                "asset_key": row.asset_key,
                "asset_id": row.asset_id,
                "mime_type": row.mime_type,
                "bytes_hash": row.bytes_hash,
                "rights_status": row.rights_status,
                "source_asset_ref": row.source_asset_ref,
                "approved": row.approved,
            }
            for row in asset_rows
        ],
        review_events=[
            DerivativeVisualReviewEventView(
                action=row.action,
                actor_source=row.actor_source,
                actor=row.actor,
                reason=row.reason,
                event_key=row.event_key,
                from_review_state=row.from_review_state,
                to_review_state=row.to_review_state,
            )
            for row in review_rows
        ],
    )


async def _version(
    db: AsyncSession, *, owner_id: int, novel_id: int, version_id: int
) -> DerivativeVisualVersion | None:
    return await db.scalar(
        select(DerivativeVisualVersion).where(
            DerivativeVisualVersion.owner_id == owner_id,
            DerivativeVisualVersion.novel_id == novel_id,
            DerivativeVisualVersion.id == version_id,
        )
    )


__all__ = [
    "DerivativeVisualLineageError",
    "DerivativeVisualScopeMismatchError",
    "DerivativeVisualVersionNotFoundError",
    "apply_review",
    "list_versions",
    "load_version_view",
]
