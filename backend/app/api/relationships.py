"""
Phase 09 owner/version/spoiler-scoped relationship graph API.

Routes never accept client owner_id. Version selection is re-proven server-side.
Legacy CharacterRelation is not used. Phase 10/11 product routes are not created.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.relationship import (
    CharacterIdentityOverrideCreate,
    CharacterIdentityOverrideResponse,
    RelationshipEvidenceResponse,
    RelationshipGraphEnvelope,
    RelationshipOverrideCreate,
    RelationshipOverrideResponse,
    RelationshipVersionSource,
)
from app.services.relationships.overrides import (
    OverrideNotFoundError,
    OverrideValidationError,
    relationship_override_service,
)
from app.services.relationships.projection import (
    ProjectionConfig,
    relationship_projection_service,
)
from app.services.relationships.query import relationship_graph_query_service

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/{novel_id}/graph", response_model=RelationshipGraphEnvelope)
async def get_relationship_graph(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    source: RelationshipVersionSource = RelationshipVersionSource.ACTIVE,
    version_id: int | None = Query(default=None, gt=0),
    through_chapter: int | None = Query(default=None, gt=0),
    full_book: bool = False,
    character_id: int | None = Query(default=None, gt=0),
    relation_type: str | None = Query(default=None, max_length=32),
    include_provisional: bool = Query(
        default=False,
        description=(
            "When accepted observations exist, also include provisional "
            "timeline co-occurrence edges (edge_kind=provisional_cooccurrence). "
            "When accepted is empty, provisional is already the default surface."
        ),
    ),
) -> RelationshipGraphEnvelope:
    """Spoiler-safe graph envelope for one owned novel/version."""

    envelope = await relationship_graph_query_service.build_graph(
        db,
        novel=novel,
        owner_id=current_user.id,
        source=source,
        version_id=version_id,
        through_chapter=through_chapter,
        request_full_book=full_book,
        character_id=character_id,
        relation_type=relation_type,
        include_provisional=include_provisional,
    )
    if envelope is None:
        raise HTTPException(status_code=404, detail="relationship graph not found")
    return envelope


@router.get(
    "/{novel_id}/observations/{observation_id}/evidence",
    response_model=RelationshipEvidenceResponse,
)
async def get_visible_evidence(
    observation_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    source: RelationshipVersionSource = RelationshipVersionSource.ACTIVE,
    version_id: int | None = Query(default=None, gt=0),
    through_chapter: int | None = Query(default=None, gt=0),
    full_book: bool = False,
) -> RelationshipEvidenceResponse:
    """Evidence is only returned for observations in the visible folded set."""

    evidence = await relationship_graph_query_service.get_visible_evidence(
        db,
        novel=novel,
        owner_id=current_user.id,
        observation_id=observation_id,
        source=source,
        version_id=version_id,
        through_chapter=through_chapter,
        request_full_book=full_book,
    )
    if evidence is None:
        raise HTTPException(status_code=404, detail="relationship evidence not found")
    return evidence


@router.post(
    "/{novel_id}/overrides/character-merge",
    response_model=CharacterIdentityOverrideResponse,
)
async def create_character_merge_override(
    payload: CharacterIdentityOverrideCreate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> CharacterIdentityOverrideResponse:
    if payload.novel_id != novel.id:
        raise HTTPException(status_code=404, detail="relationship override not found")
    try:
        row = await relationship_override_service.append_character_merge(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            payload=payload,
        )
    except OverrideNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="relationship override not found"
        ) from exc
    except OverrideValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return row


@router.post(
    "/{novel_id}/overrides/relationship",
    response_model=RelationshipOverrideResponse,
)
async def create_relationship_override(
    payload: RelationshipOverrideCreate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> RelationshipOverrideResponse:
    if payload.novel_id != novel.id:
        raise HTTPException(status_code=404, detail="relationship override not found")
    try:
        row = await relationship_override_service.append_relationship_override(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            payload=payload,
        )
    except OverrideNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="relationship override not found"
        ) from exc
    except OverrideValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return row


@router.post(
    "/{novel_id}/overrides/relationship/{override_id}/relink",
    response_model=RelationshipOverrideResponse,
)
async def relink_relationship_override(
    override_id: int,
    target_version_id: int = Query(..., gt=0),
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> RelationshipOverrideResponse:
    try:
        row = await relationship_override_service.relink_override_to_version(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            override_id=override_id,
            target_version_id=target_version_id,
            override_kind="relationship",
        )
    except OverrideNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="relationship override not found"
        ) from exc
    await db.commit()
    return row  # type: ignore[return-value]


@router.get("/{novel_id}/projection/manifest")
async def get_projection_manifest(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    version_id: int | None = Query(default=None, gt=0),
    source: RelationshipVersionSource = RelationshipVersionSource.ACTIVE,
) -> dict:
    """Deterministic accepted-observation projection manifest (non-authoritative)."""

    resolved = await relationship_graph_query_service.resolve_version(
        db,
        owner_id=current_user.id,
        novel_id=novel.id,
        source=source,
        version_id=version_id,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="relationship version not found")
    return await relationship_projection_service.build_manifest(
        db,
        owner_id=current_user.id,
        novel_id=novel.id,
        version_id=resolved.version_id,
    )


@router.post("/{novel_id}/projection/replay")
async def replay_projection(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    version_id: int | None = Query(default=None, gt=0),
    source: RelationshipVersionSource = RelationshipVersionSource.ACTIVE,
    enabled: bool = False,
) -> dict:
    """Optional one-way projection replay; disabled by default."""

    resolved = await relationship_graph_query_service.resolve_version(
        db,
        owner_id=current_user.id,
        novel_id=novel.id,
        source=source,
        version_id=version_id,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="relationship version not found")
    result = await relationship_projection_service.replay_accepted_observations(
        db,
        owner_id=current_user.id,
        novel_id=novel.id,
        version_id=resolved.version_id,
        config=ProjectionConfig(enabled=enabled),
    )
    await db.commit()
    return {
        "status": result.status,
        "manifest_checksum": result.manifest_checksum,
        "observation_count": result.observation_count,
        "audit_id": result.audit_id,
        "reason": result.reason,
        "checkpoint": result.checkpoint,
    }
