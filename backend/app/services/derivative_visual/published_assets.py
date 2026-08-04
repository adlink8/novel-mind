"""Published derivative asset query seam (Phase 38-03, REQ-FORK-04).

D-38-03: only **published** derivative assets are reader-visible. This module
owns the owner-scoped read seam that returns the ``PublishedDerivativeVisualAsset``
envelope for candidates whose ``review_state`` is ``approved`` and whose
owner/novel/project/fork scope is visible to the caller.

Fail-closed rules:
- every query is scoped by owner + novel and (optionally) project/fork;
- an asset outside the scope or not in the ``approved`` state is an identical
  ``PublishedAssetNotFound`` (404-equivalent) — Original assets and unapproved
  candidates are never returned here;
- the read envelope exposes the generated ``asset_id``, the replayed content
  hash, the visual-version/source-snapshot lineage, the approval/review chain
  (consistency verdict + reasons + events) and the identity/source/generator
  lineage; no raw storage path and no Original row are ever exposed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_visual import (
    DERIVATIVE_ASSET_NAMESPACE,
    DerivativeVisualCandidateAsset,
    DerivativeVisualCandidateReviewEvent,
    DerivativeVisualVersion,
)
from app.schemas.derivative_visual_asset import (
    DerivativeAssetIdentityRow,
    DerivativeAssetReviewEnvelope,
    DerivativeAssetReviewEventView,
    DerivativeAssetSourceRef,
    DerivativeConsistencyReport,
    DerivativeConsistencyVerdict,
    DerivativeSourceSnapshotRef,
    DerivativeVisualAssetState,
    DerivativeVisualAssetView,
    DerivativeVisualVersionRef,
    PublishedDerivativeVisualAsset,
)


class PublishedAssetNotFound(ValueError):
    """A published asset outside the explicit owner/novel scope (404-equivalent)."""


class PublishedAssetScopeError(ValueError):
    """Scope identifiers must be explicit positive integers."""


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise PublishedAssetScopeError(
            "scope identifiers must be explicit positive integers"
        )


def _candidate_query(
    owner_id: int,
    novel_id: int,
    *,
    project_id: int | None,
    fork_id: int | None,
    approved_only: bool,
):
    query = (
        select(DerivativeVisualCandidateAsset)
        .join(
            DerivativeVisualVersion,
            (DerivativeVisualVersion.owner_id == DerivativeVisualCandidateAsset.owner_id)
            & (DerivativeVisualVersion.novel_id == DerivativeVisualCandidateAsset.novel_id)
            & (DerivativeVisualVersion.id == DerivativeVisualCandidateAsset.visual_version_id),
        )
        .where(
            DerivativeVisualCandidateAsset.owner_id == owner_id,
            DerivativeVisualCandidateAsset.novel_id == novel_id,
        )
    )
    if project_id is not None:
        query = query.where(DerivativeVisualVersion.project_id == project_id)
    if fork_id is not None:
        query = query.where(DerivativeVisualVersion.fork_id == fork_id)
    if approved_only:
        query = query.where(
            DerivativeVisualCandidateAsset.review_state
            == DerivativeVisualAssetState.APPROVED.value
        )
    return query


async def list_published_assets(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int | None = None,
    fork_id: int | None = None,
) -> list[PublishedDerivativeVisualAsset]:
    """List every approved derivative asset visible in the owner/novel scope.

    ``project_id``/``fork_id`` narrow the visual-fork lineage when supplied.
    Only ``approved`` candidates are returned; Original assets and unapproved
    candidates are never included (REQ-FORK-04 / D-38-03).
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    query = _candidate_query(
        owner_id,
        novel_id,
        project_id=project_id,
        fork_id=fork_id,
        approved_only=True,
    ).order_by(
        DerivativeVisualCandidateAsset.chapter_number.asc(),
        DerivativeVisualCandidateAsset.id.asc(),
    )
    rows = (await db.scalars(query)).all()
    return [await published_asset_view(db, row) for row in rows]


async def load_published_asset(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    asset_id: str,
) -> PublishedDerivativeVisualAsset:
    """Load one published asset by generated ``asset_id``.

    A candidate that is missing, outside the owner/novel scope or not in the
    ``approved`` state is an identical ``PublishedAssetNotFound`` — an
    unapproved candidate is indistinguishable from "not found".
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    row = await db.scalar(
        select(DerivativeVisualCandidateAsset).where(
            DerivativeVisualCandidateAsset.owner_id == owner_id,
            DerivativeVisualCandidateAsset.novel_id == novel_id,
            DerivativeVisualCandidateAsset.asset_id == asset_id,
            DerivativeVisualCandidateAsset.review_state
            == DerivativeVisualAssetState.APPROVED.value,
        )
    )
    if row is None:
        raise PublishedAssetNotFound(
            "published derivative asset not found in the owner/novel scope"
        )
    return await derivative_asset_view(db, row)


async def derivative_asset_view(
    db: AsyncSession,
    row: DerivativeVisualCandidateAsset,
) -> DerivativeVisualAssetView:
    """Build the candidate read envelope (any review state, owner-scoped).

    Exposed by the store/consistency/review seams; the published query uses the
    ``PublishedDerivativeVisualAsset`` projection and only ever sees approved
    candidates. No raw storage path and no Original row is ever exposed.
    """
    version = await db.scalar(
        select(DerivativeVisualVersion).where(
            DerivativeVisualVersion.owner_id == row.owner_id,
            DerivativeVisualVersion.novel_id == row.novel_id,
            DerivativeVisualVersion.id == row.visual_version_id,
        )
    )
    if version is None:
        raise PublishedAssetNotFound(
            "derivative asset visual version is missing in scope"
        )
    review_rows = (
        await db.scalars(
            select(DerivativeVisualCandidateReviewEvent)
            .where(
                DerivativeVisualCandidateReviewEvent.owner_id == row.owner_id,
                DerivativeVisualCandidateReviewEvent.novel_id == row.novel_id,
                DerivativeVisualCandidateReviewEvent.candidate_id == row.id,
            )
            .order_by(DerivativeVisualCandidateReviewEvent.id.asc())
        )
    ).all()

    consistency_report = None
    if row.consistency_report:
        consistency_report = DerivativeConsistencyReport.model_validate(
            dict(row.consistency_report)
        )
    reasons = list(consistency_report.reasons if consistency_report else [])
    review = DerivativeAssetReviewEnvelope(
        review_state=DerivativeVisualAssetState(row.review_state),
        consistency_verdict=DerivativeConsistencyVerdict(row.consistency_verdict),
        consistency_report=consistency_report,
        reasons=reasons,
        review_events=[
            DerivativeAssetReviewEventView(
                action=ev.action,
                actor_source=ev.actor_source,
                actor=ev.actor,
                reason=ev.reason,
                event_key=ev.event_key,
                from_review_state=DerivativeVisualAssetState(ev.from_review_state),
                to_review_state=DerivativeVisualAssetState(ev.to_review_state),
            )
            for ev in review_rows
        ],
    )
    return DerivativeVisualAssetView(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        project_id=row.project_id,
        fork_id=row.fork_id,
        asset_id=row.asset_id,
        asset_key=row.asset_key,
        content_hash=row.content_hash,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        namespace=DERIVATIVE_ASSET_NAMESPACE,
        scene_spec_hash=row.scene_spec_hash,
        chapter_number=row.chapter_number,
        visual_version=DerivativeVisualVersionRef(
            version_id=version.id,
            version_key=version.version_key,
            version_hash=version.canonical_payload_hash,
        ),
        source_snapshot=DerivativeSourceSnapshotRef(
            source_snapshot_id=row.source_snapshot_id,
            source_snapshot_hash=row.source_snapshot_hash,
            source_manifest_hash=row.source_manifest_hash,
            cutoff_chapter=row.cutoff_chapter,
        ),
        approval=DerivativeVisualAssetState(row.review_state),
        review=review,
        source_refs=[
            DerivativeAssetSourceRef.model_validate(dict(ref))
            for ref in (row.source_refs or [])
        ],
        identity_lineage=[
            DerivativeAssetIdentityRow.model_validate(dict(item))
            for item in (row.identity_lineage or [])
        ],
        generator_lineage=dict(row.generator_lineage or {}),
        divergence_manifest_hash=row.divergence_manifest_hash,
    )


async def published_asset_view(
    db: AsyncSession,
    row: DerivativeVisualCandidateAsset,
) -> PublishedDerivativeVisualAsset:
    """Published projection: the candidate envelope cast for approved-only reads."""
    view = await derivative_asset_view(db, row)
    return PublishedDerivativeVisualAsset(**view.model_dump(mode="json"))


__all__ = [
    "DERIVATIVE_ASSET_NAMESPACE",
    "PublishedAssetNotFound",
    "PublishedAssetScopeError",
    "derivative_asset_view",
    "list_published_assets",
    "load_published_asset",
    "published_asset_view",
]
