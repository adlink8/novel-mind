"""Reconstruct immutable Visual Bible contracts from persisted rows.

Extracted from the scene-spec service seam (``SceneSpecService``): loading a
``VisualBibleVersionContract`` is Visual-Bible-domain logic — it replays
entity/claim/evidence/asset rows into the frozen contract and fails closed
when the persisted manifest hash does not replay.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visual_bible import (
    VisualBibleVersion as VisualBibleVersionRow,
    VisualClaim as VisualClaimRow,
    VisualEntity as VisualEntityRow,
    VisualEvidenceRef as VisualEvidenceRefRow,
    VisualReferenceAsset as VisualReferenceAssetRow,
)
from app.schemas.visual_bible import (
    VisualBibleVersionContract,
    VisualClaimContract,
    VisualEntityContract,
    VisualEvidenceRef,
    VisualReferenceAssetContract,
    VisualReviewState,
    recompute_manifest_hash,
)

from .errors import SceneSpecServiceError


async def load_visual_bible_contract(
    session: AsyncSession,
    version: VisualBibleVersionRow,
) -> VisualBibleVersionContract:
    """Reconstruct the immutable Visual Bible contract from persisted rows."""
    entity_rows = (
        await session.scalars(
            select(VisualEntityRow)
            .where(
                VisualEntityRow.owner_id == version.owner_id,
                VisualEntityRow.novel_id == version.novel_id,
                VisualEntityRow.version_id == version.id,
            )
            .order_by(VisualEntityRow.id.asc())
        )
    ).all()
    entity_contracts = [
        VisualEntityContract(
            stable_id=row.stable_id,
            entity_key=row.entity_key,
            entity_type=row.entity_type,
            description=row.description,
            authority=row.authority,
            disclosure_cutoff=row.disclosure_cutoff,
        )
        for row in entity_rows
    ]
    claim_rows = (
        await session.scalars(
            select(VisualClaimRow)
            .where(
                VisualClaimRow.owner_id == version.owner_id,
                VisualClaimRow.novel_id == version.novel_id,
                VisualClaimRow.version_id == version.id,
            )
            .order_by(VisualClaimRow.id.asc())
        )
    ).all()
    evidence_rows = (
        await session.scalars(
            select(VisualEvidenceRefRow)
            .where(
                VisualEvidenceRefRow.owner_id == version.owner_id,
                VisualEvidenceRefRow.novel_id == version.novel_id,
                VisualEvidenceRefRow.version_id == version.id,
            )
            .order_by(VisualEvidenceRefRow.id.asc())
        )
    ).all()
    evidence_by_claim: dict[int, list[VisualEvidenceRef]] = {}
    for row in evidence_rows:
        evidence_by_claim.setdefault(row.claim_id, []).append(
            VisualEvidenceRef(
                evidence_key=row.evidence_key,
                source_snapshot_id=row.source_snapshot_id,
                source_snapshot_hash=row.source_snapshot_hash,
                chapter_id=row.chapter_id,
                chapter_number=row.chapter_number,
                source_start=row.source_start,
                source_end=row.source_end,
                content_hash=row.content_hash,
                excerpt=row.excerpt,
                cutoff_chapter=row.cutoff_chapter,
            )
        )
    claim_contracts = [
        VisualClaimContract(
            claim_key=row.claim_key,
            entity_stable_id=row.entity_stable_id,
            authority=row.authority,
            description=row.description,
            author=row.author,
            rationale=row.rationale,
            cutoff_chapter=row.cutoff_chapter,
            claim_hash=row.claim_hash,
            evidence_refs=evidence_by_claim.get(row.id, []),
        )
        for row in claim_rows
    ]
    asset_rows = (
        await session.scalars(
            select(VisualReferenceAssetRow)
            .where(
                VisualReferenceAssetRow.owner_id == version.owner_id,
                VisualReferenceAssetRow.novel_id == version.novel_id,
                VisualReferenceAssetRow.version_id == version.id,
            )
            .order_by(VisualReferenceAssetRow.id.asc())
        )
    ).all()
    asset_contracts = [
        VisualReferenceAssetContract(
            asset_key=row.asset_key,
            asset_id=row.asset_id,
            mime_type=row.mime_type,
            bytes_hash=row.bytes_hash,
            rights_status=row.rights_status,
            provenance=row.provenance,
        )
        for row in asset_rows
    ]
    contract = VisualBibleVersionContract(
        schema_version=version.schema_version,
        artifact_kind="visual_bible",
        owner_id=version.owner_id,
        novel_id=version.novel_id,
        version_key=version.version_key,
        revision_number=version.revision_number,
        parent_version_id=version.parent_version_id,
        source_snapshot_id=version.source_snapshot_id,
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff_chapter=version.cutoff_chapter,
        schema_hash=version.schema_hash,
        policy_hash=version.policy_hash,
        prompt_hash=version.prompt_hash,
        model_hash=version.model_hash,
        config_hash=version.config_hash,
        manifest_hash=version.manifest_hash,
        style_profile=version.style_profile,
        constraints=version.constraints,
        entities=entity_contracts,
        claims=claim_contracts,
        reference_assets=asset_contracts,
        review_state=VisualReviewState(version.review_state),
    )
    if contract.manifest_hash != recompute_manifest_hash(contract):
        raise SceneSpecServiceError(
            "persisted Visual Bible revision does not replay its manifest hash"
        )
    return contract
