"""Phase 38-01 explicit derivative Visual Bible fork (D-38-01/D-38-02).

The only write seam that creates a derivative Visual Bible revision: a narrow
owner-scoped transaction that forks one **Original** Visual Bible snapshot into
the sealed ``fanfiction_visual`` namespace. Fail-closed rules:

- the source ``visual_bible_versions`` row must exist inside the same
  owner/novel scope; the derivative version's ``source_snapshot_hash`` and
  ``source_manifest_hash`` must match that exact Original snapshot, so an
  Original row is referenced read-only and can never be overwritten
  (REQ-FORK-04);
- the derivative namespace, the explicit ``divergence`` declaration, the
  owner/project/fork provenance and the source refs are sealed at create
  (D-38-01/D-38-02); a foreign/missing project or fork is an identical 404
  and a project with status ``archived`` cannot anchor a fork;
- a unique ``version_key`` is immutable: a conflicting retry fails closed
  instead of duplicating or overwriting the first fork; an identical retry
  only replays the existing revision.

Nothing in this module writes to the Original Visual Bible tables.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canon_fork import CanonFork
from app.models.derivative_project import (
    DERIVATIVE_PROJECT_USABLE_FORK_STATUSES,
    DerivativeProject,
)
from app.models.derivative_visual import (
    DERIVATIVE_VISUAL_NAMESPACE,
    DerivativeVisualAsset,
    DerivativeVisualEntity,
    DerivativeVisualVersion,
)
from app.models.visual_bible import VisualBibleVersion
from app.schemas.derivative_visual import (
    DerivativeVisualGateError,
    DerivativeVisualVersionContract,
    derivative_visual_manifest_payload,
    recompute_derivative_visual_manifest_hash,
    validate_derivative_visual_fork_contract,
)


class DerivativeVisualForkError(ValueError):
    """Fail-closed derivative visual fork gate violation."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class PersistedForkRevision:
    """Immutable fork write result with the persisted child row ids."""

    version: DerivativeVisualVersion
    entity_ids: dict[str, int]
    asset_ids: dict[str, int]
    replayed: bool


def _version_idempotency_key(version: DerivativeVisualVersionContract) -> str:
    from app.schemas.derivative_visual import canonical_derivative_visual_hash

    return canonical_derivative_visual_hash(
        {
            "kind": "derivative_visual_version",
            "owner_id": version.owner_id,
            "novel_id": version.novel_id,
            "version_key": version.version_key,
            "manifest_hash": version.manifest_hash,
        }
    )


def _child_idempotency_key(
    *,
    kind: str,
    owner_id: int,
    novel_id: int,
    version_key: str,
    child_key: str,
    payload_hash: str,
) -> str:
    from app.schemas.derivative_visual import canonical_derivative_visual_hash

    return canonical_derivative_visual_hash(
        {
            "kind": kind,
            "owner_id": owner_id,
            "novel_id": novel_id,
            "version_key": version_key,
            "key": child_key,
            "payload_hash": payload_hash,
        }
    )


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeVisualForkError(
            "invalid_scope", "scope identifiers must be explicit positive integers"
        )


async def create_derivative_visual_fork(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version: DerivativeVisualVersionContract,
) -> PersistedForkRevision:
    """Fork one Original Visual Bible snapshot into the derivative namespace.

    Raises before writing anything when the scope, project/fork ownership, the
    source snapshot hash/manifest lineage or the strict fork contract do not
    hold. The source Original snapshot is never mutated.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if version.owner_id != owner_id or version.novel_id != novel_id:
        raise DerivativeVisualForkError(
            "scope_mismatch", "version scope does not match request scope"
        )

    project = await db.scalar(
        select(DerivativeProject).where(
            DerivativeProject.id == version.project_id,
            DerivativeProject.owner_id == owner_id,
            DerivativeProject.novel_id == novel_id,
        )
    )
    if project is None:
        raise DerivativeVisualForkError(
            "project_not_found",
            "derivative project not found in the owner/novel scope",
        )
    if project.status != "active":
        raise DerivativeVisualForkError(
            "project_not_usable",
            f"derivative project {project.id} is {project.status!r}; "
            "an archived project cannot anchor a visual fork",
        )

    fork = await db.scalar(
        select(CanonFork).where(
            CanonFork.id == version.fork_id,
            CanonFork.owner_id == owner_id,
            CanonFork.novel_id == novel_id,
        )
    )
    if fork is None:
        raise DerivativeVisualForkError(
            "fork_not_found", "canon fork not found in the owner/novel scope"
        )
    if fork.status not in DERIVATIVE_PROJECT_USABLE_FORK_STATUSES:
        raise DerivativeVisualForkError(
            "fork_not_usable",
            f"fork {fork.id} is {fork.status!r}; rejected/archived forks cannot "
            "anchor a derivative visual fork",
        )

    source = await db.scalar(
        select(VisualBibleVersion).where(
            VisualBibleVersion.id == version.source_version_id,
            VisualBibleVersion.owner_id == owner_id,
            VisualBibleVersion.novel_id == novel_id,
        )
    )
    if source is None:
        raise DerivativeVisualForkError(
            "source_version_not_found",
            "original visual bible version not found in the owner/novel scope",
        )
    if version.source_snapshot_hash != source.source_snapshot_hash:
        raise DerivativeVisualForkError(
            "source_snapshot_hash_mismatch",
            "derivative source_snapshot_hash does not match the original snapshot",
        )
    if version.source_manifest_hash != source.manifest_hash:
        raise DerivativeVisualForkError(
            "source_manifest_hash_mismatch",
            "derivative source_manifest_hash does not match the original snapshot",
        )
    if version.cutoff_chapter != source.cutoff_chapter:
        raise DerivativeVisualForkError(
            "cutoff_chapter_mismatch",
            "derivative cutoff_chapter must match the original snapshot cutoff",
        )

    # D-38-01: the derivative namespace is sealed; D-38-02 requires an explicit
    # divergence declaration. Both fail closed before any row is written.
    if version.namespace != DERIVATIVE_VISUAL_NAMESPACE:
        raise DerivativeVisualForkError(
            "namespace_denied",
            f"only the {DERIVATIVE_VISUAL_NAMESPACE!r} namespace is a derivative "
            "visual write target",
        )
    try:
        validate_derivative_visual_fork_contract(version)
    except DerivativeVisualGateError as exc:
        raise DerivativeVisualForkError("gate_violation", str(exc)) from exc

    existing = await db.scalar(
        select(DerivativeVisualVersion).where(
            DerivativeVisualVersion.owner_id == owner_id,
            DerivativeVisualVersion.novel_id == novel_id,
            DerivativeVisualVersion.version_key == version.version_key,
        )
    )
    if existing is not None:
        _require_identical_fork(existing, version)
        return await _reload_persisted(db, existing, replayed=True)

    projection_hash = recompute_derivative_visual_manifest_hash(version)
    version_row = DerivativeVisualVersion(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=version.project_id,
        fork_id=version.fork_id,
        visual_namespace=DERIVATIVE_VISUAL_NAMESPACE,
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
        review_state=version.review_state.value,
        schema_version=version.schema_version,
        schema_hash=version.schema_hash,
        policy_hash=version.policy_hash,
        prompt_hash=version.prompt_hash,
        model_hash=version.model_hash,
        config_hash=version.config_hash,
        manifest_hash=version.manifest_hash,
        style_profile=version.style_profile,
        constraints=version.constraints,
        canonical_payload=derivative_visual_manifest_payload(version),
        canonical_payload_hash=projection_hash,
        idempotency_key=_version_idempotency_key(version),
        projection_hash=projection_hash,
    )
    db.add(version_row)
    entity_ids: dict[str, int] = {}
    asset_ids: dict[str, int] = {}
    try:
        await db.flush()
        entity_ids = await _persist_entities(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            version=version,
            version_row=version_row,
            projection_hash=projection_hash,
        )
        asset_ids = await _persist_assets(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            version=version,
            version_row=version_row,
            projection_hash=projection_hash,
        )
    except IntegrityError:
        # Concurrent duplicate create: roll back and replay the winner; a
        # conflicting retry still fails closed instead of duplicating rows.
        await db.rollback()
        existing = await db.scalar(
            select(DerivativeVisualVersion).where(
                DerivativeVisualVersion.owner_id == owner_id,
                DerivativeVisualVersion.novel_id == novel_id,
                DerivativeVisualVersion.version_key == version.version_key,
            )
        )
        if existing is None:
            raise DerivativeVisualForkError(
                "fork_race",
                "derivative visual fork race: existing row not found after rollback",
            )
        _require_identical_fork(existing, version)
        return await _reload_persisted(db, existing, replayed=True)
    return PersistedForkRevision(
        version=version_row,
        entity_ids=entity_ids,
        asset_ids=asset_ids,
        replayed=False,
    )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def _persist_entities(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version: DerivativeVisualVersionContract,
    version_row: DerivativeVisualVersion,
    projection_hash: str,
) -> dict[str, int]:
    from app.schemas.derivative_visual import canonical_derivative_visual_hash

    entity_ids: dict[str, int] = {}
    for entity in version.entities:
        payload = entity.model_dump(mode="json")
        payload_hash = canonical_derivative_visual_hash(payload)
        row = DerivativeVisualEntity(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_row.id,
            entity_key=entity.entity_key,
            stable_id=entity.stable_id,
            entity_type=entity.entity_type.value,
            disclosure_cutoff=entity.disclosure_cutoff,
            authority=entity.authority,
            divergence=entity.divergence,
            source_entity_ref=entity.source_entity_ref,
            description=entity.description,
            canonical_payload=payload,
            canonical_payload_hash=payload_hash,
            idempotency_key=_child_idempotency_key(
                kind="derivative_visual_entity",
                owner_id=owner_id,
                novel_id=novel_id,
                version_key=version.version_key,
                child_key=entity.stable_id,
                payload_hash=payload_hash,
            ),
            projection_hash=projection_hash,
            schema_version=version.schema_version,
        )
        db.add(row)
        await db.flush()
        entity_ids[entity.stable_id] = row.id
    return entity_ids


async def _persist_assets(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version: DerivativeVisualVersionContract,
    version_row: DerivativeVisualVersion,
    projection_hash: str,
) -> dict[str, int]:
    from app.schemas.derivative_visual import canonical_derivative_visual_hash

    asset_ids: dict[str, int] = {}
    for asset in version.reference_assets:
        payload = asset.model_dump(mode="json")
        payload_hash = canonical_derivative_visual_hash(payload)
        row = DerivativeVisualAsset(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_row.id,
            asset_key=asset.asset_key,
            asset_id=asset.asset_id,
            mime_type=asset.mime_type,
            bytes_hash=asset.bytes_hash,
            rights_status=asset.rights_status.value,
            source_asset_ref=asset.source_asset_ref,
            provenance=asset.provenance,
            approved=False,  # derivative assets never silently canon
            canonical_payload=payload,
            canonical_payload_hash=payload_hash,
            idempotency_key=_child_idempotency_key(
                kind="derivative_visual_asset",
                owner_id=owner_id,
                novel_id=novel_id,
                version_key=version.version_key,
                child_key=asset.asset_key,
                payload_hash=payload_hash,
            ),
            projection_hash=projection_hash,
            schema_version=version.schema_version,
        )
        db.add(row)
        await db.flush()
        asset_ids[asset.asset_key] = row.id
    return asset_ids


async def _reload_persisted(
    db: AsyncSession,
    version: DerivativeVisualVersion,
    *,
    replayed: bool,
) -> PersistedForkRevision:
    entity_ids = {
        row.stable_id: row.id
        for row in (
            await db.scalars(
                select(DerivativeVisualEntity).where(
                    DerivativeVisualEntity.owner_id == version.owner_id,
                    DerivativeVisualEntity.novel_id == version.novel_id,
                    DerivativeVisualEntity.version_id == version.id,
                )
            )
        ).all()
    }
    asset_ids = {
        row.asset_key: row.id
        for row in (
            await db.scalars(
                select(DerivativeVisualAsset).where(
                    DerivativeVisualAsset.owner_id == version.owner_id,
                    DerivativeVisualAsset.novel_id == version.novel_id,
                    DerivativeVisualAsset.version_id == version.id,
                )
            )
        ).all()
    }
    return PersistedForkRevision(
        version=version, entity_ids=entity_ids, asset_ids=asset_ids, replayed=replayed
    )


def _require_identical_fork(
    existing: DerivativeVisualVersion,
    version: DerivativeVisualVersionContract,
) -> None:
    """A retried fork with the same version_key must be byte-identical."""
    if (
        existing.canonical_payload_hash
        != recompute_derivative_visual_manifest_hash(version)
        or existing.manifest_hash != version.manifest_hash
        or existing.source_version_id != version.source_version_id
        or existing.source_snapshot_hash != version.source_snapshot_hash
        or existing.source_manifest_hash != version.source_manifest_hash
    ):
        raise DerivativeVisualForkError(
            "fork_conflict",
            "conflicting fork retry: version_key already exists with different "
            "immutable content",
        )


__all__ = [
    "DERIVATIVE_VISUAL_NAMESPACE",
    "DerivativeVisualForkError",
    "PersistedForkRevision",
    "create_derivative_visual_fork",
]
