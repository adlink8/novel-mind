"""Visual Bible candidate authority seam (Phase 30-02, REQ-VIS-01).

D-30-01..D-30-04: a Visual Bible revision is an immutable, versioned candidate
until an explicit append-only human/machine review action selects a state for
the owning novel. This module owns:

- ``VisualBibleAuthorityService.create_revision`` — the narrow write seam that
  persists one validated candidate (version + entities + claims + evidence refs
  + reference assets) inside an explicit owner/novel scope. Content rows are
  append-only; a unique idempotency key only replays an identical existing row
  and never creates a second revision.
- ``VisualBibleAuthorityService.apply_review`` — append-only, explicit and
  idempotent review actions (approve/reject/edit/supersede/needs_relink) whose
  legality is decided server-side from the version's current review state.
- ``load_version_view`` / ``list_versions`` — owner-scoped read seams that
  expose authority labels, evidence, review state and rights/provenance without
  leaking any row outside the owner/novel/version boundary.

Fail-closed rules:
- no evidence / unresolved canon claim / wrong hash / duplicate stable id /
  illegal transition raises before any row is written;
- a generated or unreviewed reference asset never becomes canon (``approved``
  stays False; approval only moves the candidate review state);
- ``Novel.cover_url`` and the storage images seam are never touched (D-30-01).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models.novel import Novel
from app.models.visual_bible import (
    VisualBibleReviewEvent,
    VisualBibleVersion,
    VisualClaim,
    VisualEntity,
    VisualEvidenceRef as VisualEvidenceRefModel,
    VisualReferenceAsset,
)
from app.schemas.visual_bible import (
    VisualAuthority,
    VisualBibleGateError,
    VisualBibleVersionContract,
    VisualBibleVersionView,
    VisualClaimView,
    VisualEntityView,
    VisualEvidenceRef,
    VisualEvidenceRefView,
    VisualReferenceAssetView,
    VisualReviewEventInput,
    VisualReviewEventView,
    VisualReviewState,
    canonical_claim_payload,
    canonical_visual_hash,
    recompute_manifest_hash,
    validate_review_event,
    validate_version_contract,
    version_manifest_payload,
)


class VisualBibleAuthorityError(ValueError):
    """Base class for fail-closed visual bible authority errors."""


class ScopeMismatchError(VisualBibleAuthorityError):
    pass


class GateViolationError(VisualBibleAuthorityError):
    pass


class CandidateConflictError(VisualBibleAuthorityError):
    pass


class CandidateNotFoundError(VisualBibleAuthorityError):
    pass


@dataclass(frozen=True)
class PersistedRevision:
    """Immutable candidate write result with the persisted child row ids."""

    version: VisualBibleVersion
    entity_ids: dict[str, int]
    claim_ids: dict[str, int]
    replayed: bool


def _version_idempotency_key(version: VisualBibleVersionContract) -> str:
    return canonical_visual_hash(
        {
            "kind": "visual_bible_version",
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
    return canonical_visual_hash(
        {
            "kind": kind,
            "owner_id": owner_id,
            "novel_id": novel_id,
            "version_key": version_key,
            "key": child_key,
            "payload_hash": payload_hash,
        }
    )


class VisualBibleAuthorityService:
    """Narrow write seam: candidate revisions and append-only review actions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_revision(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version: VisualBibleVersionContract,
        verified_evidence: Mapping[str, tuple[VisualEvidenceRef, ...]],
    ) -> PersistedRevision:
        """Persist one validated candidate revision, or replay an identical one.

        Raises before writing anything when the scope, novel ownership, strict
        contract gates, unresolved canon evidence or parent lineage do not hold.
        """
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        if version.owner_id != owner_id or version.novel_id != novel_id:
            raise ScopeMismatchError("version scope does not match request scope")

        novel = await self._session.scalar(
            select(Novel).where(Novel.id == novel_id, Novel.owner_id == owner_id)
        )
        if novel is None:
            raise ScopeMismatchError("owner does not own the novel")

        if version.review_state is not VisualReviewState.CANDIDATE:
            raise GateViolationError(
                "only candidate revisions can be created; "
                "approval is an explicit append-only review action"
            )

        if version.parent_version_id is not None:
            parent = await self._version(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version.parent_version_id,
            )
            if parent is None:
                raise ScopeMismatchError(
                    "parent version is outside the owner/novel candidate scope"
                )

        # Strict typed contract + immutable lineage gates (D-30-02/03).
        try:
            validate_version_contract(version)
        except VisualBibleGateError as exc:
            raise GateViolationError(str(exc)) from exc

        # Every canon_fact claim must carry server-verified leaf evidence.
        self._require_verified_evidence(version, verified_evidence)

        existing = await self._session.scalar(
            select(VisualBibleVersion).where(
                VisualBibleVersion.owner_id == owner_id,
                VisualBibleVersion.novel_id == novel_id,
                VisualBibleVersion.version_key == version.version_key,
            )
        )
        if existing is not None:
            self._require_identical_version(existing, version)
            return await self._reload_persisted(existing, replayed=True)

        projection_hash = recompute_manifest_hash(version)
        version_row = VisualBibleVersion(
            owner_id=owner_id,
            novel_id=novel_id,
            version_key=version.version_key,
            revision_number=version.revision_number,
            parent_version_id=version.parent_version_id,
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
            review_state=VisualReviewState.CANDIDATE.value,
            schema_version=version.schema_version,
            schema_hash=version.schema_hash,
            policy_hash=version.policy_hash,
            prompt_hash=version.prompt_hash,
            model_hash=version.model_hash,
            config_hash=version.config_hash,
            manifest_hash=version.manifest_hash,
            style_profile=version.style_profile,
            constraints=version.constraints,
            canonical_payload=version_manifest_payload(version),
            canonical_payload_hash=projection_hash,
            idempotency_key=_version_idempotency_key(version),
            projection_hash=projection_hash,
        )
        self._session.add(version_row)
        entity_ids: dict[str, int] = {}
        claim_ids: dict[str, int] = {}
        try:
            await self._session.flush()
            entity_ids = await self._persist_entities(
                owner_id=owner_id,
                novel_id=novel_id,
                version=version,
                version_row=version_row,
                projection_hash=projection_hash,
            )
            claim_ids = await self._persist_claims(
                owner_id=owner_id,
                novel_id=novel_id,
                version=version,
                version_row=version_row,
                entity_ids=entity_ids,
                projection_hash=projection_hash,
            )
            await self._persist_assets(
                owner_id=owner_id,
                novel_id=novel_id,
                version=version,
                version_row=version_row,
                projection_hash=projection_hash,
            )
        except IntegrityError:
            # Concurrent duplicate create: roll back and replay the winner; a
            # conflicting retry still fails closed instead of duplicating rows.
            await self._session.rollback()
            existing = await self._session.scalar(
                select(VisualBibleVersion).where(
                    VisualBibleVersion.owner_id == owner_id,
                    VisualBibleVersion.novel_id == novel_id,
                    VisualBibleVersion.version_key == version.version_key,
                )
            )
            if existing is None:
                raise CandidateConflictError(
                    "candidate version race: existing row not found after rollback"
                )
            self._require_identical_version(existing, version)
            return await self._reload_persisted(existing, replayed=True)
        return PersistedRevision(
            version=version_row,
            entity_ids=entity_ids,
            claim_ids=claim_ids,
            replayed=False,
        )

    async def apply_review(
        self,
        *,
        owner_id: int,
        novel_id: int,
        event: VisualReviewEventInput,
    ) -> VisualBibleVersion:
        """Append an explicit, idempotent review action and update the projection.

        A repeated ``event_key`` (retried approval) only replays the existing
        state; it never appends a second event and never creates a second
        approval (D-30-04).
        """
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        if event.owner_id != owner_id or event.novel_id != novel_id:
            raise ScopeMismatchError("review event scope does not match request scope")

        version = await self._version(
            owner_id=owner_id, novel_id=novel_id, version_id=event.version_id
        )
        if version is None:
            raise CandidateNotFoundError(
                "candidate version not found in explicit owner/novel scope"
            )

        existing = await self._session.scalar(
            select(VisualBibleReviewEvent).where(
                VisualBibleReviewEvent.owner_id == owner_id,
                VisualBibleReviewEvent.novel_id == novel_id,
                VisualBibleReviewEvent.version_id == version.id,
                VisualBibleReviewEvent.event_key == event.event_key,
            )
        )
        if existing is not None:
            return version  # idempotent replay: no second event, no state change

        if version.review_state != event.from_review_state.value:
            raise GateViolationError(
                f"review from_review_state {event.from_review_state.value!r} does not "
                f"match the version's current state {version.review_state!r}"
            )

        try:
            to_state = validate_review_event(event)
        except VisualBibleGateError as exc:
            raise GateViolationError(str(exc)) from exc

        review_row = VisualBibleReviewEvent(
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
            details={},
        )
        self._session.add(review_row)
        version.review_state = to_state.value
        try:
            await self._session.flush()
        except IntegrityError:
            # Concurrent duplicate event_key: roll back and replay the winner.
            await self._session.rollback()
            version = await self._version(
                owner_id=owner_id, novel_id=novel_id, version_id=event.version_id
            )
            if version is None:
                raise CandidateNotFoundError(
                    "candidate version disappeared during review replay"
                )
            return version
        return version

    # ------------------------------------------------------------------ helpers

    async def _persist_entities(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version: VisualBibleVersionContract,
        version_row: VisualBibleVersion,
        projection_hash: str,
    ) -> dict[str, int]:
        entity_ids: dict[str, int] = {}
        for entity in version.entities:
            payload = entity.model_dump(mode="json")
            payload_hash = canonical_visual_hash(payload)
            row = VisualEntity(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version_row.id,
                entity_key=entity.entity_key,
                stable_id=entity.stable_id,
                entity_type=entity.entity_type.value,
                disclosure_cutoff=entity.disclosure_cutoff,
                authority=entity.authority.value,
                description=entity.description,
                canonical_payload=payload,
                canonical_payload_hash=payload_hash,
                idempotency_key=_child_idempotency_key(
                    kind="visual_bible_entity",
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_key=version.version_key,
                    child_key=entity.stable_id,
                    payload_hash=payload_hash,
                ),
                projection_hash=projection_hash,
                schema_version=version.schema_version,
            )
            self._session.add(row)
            await self._session.flush()
            entity_ids[entity.stable_id] = row.id
        return entity_ids

    async def _persist_claims(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version: VisualBibleVersionContract,
        version_row: VisualBibleVersion,
        entity_ids: Mapping[str, int],
        projection_hash: str,
    ) -> dict[str, int]:
        claim_ids: dict[str, int] = {}
        for claim in version.claims:
            payload = canonical_claim_payload(claim)
            payload_hash = canonical_visual_hash(payload)
            row = VisualClaim(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version_row.id,
                claim_key=claim.claim_key,
                entity_id=entity_ids[claim.entity_stable_id],
                entity_stable_id=claim.entity_stable_id,
                authority=claim.authority.value,
                description=claim.description,
                author=claim.author,
                rationale=claim.rationale,
                cutoff_chapter=claim.cutoff_chapter,
                claim_hash=claim.claim_hash,
                canonical_payload=payload,
                canonical_payload_hash=payload_hash,
                idempotency_key=_child_idempotency_key(
                    kind="visual_bible_claim",
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_key=version.version_key,
                    child_key=claim.claim_key,
                    payload_hash=payload_hash,
                ),
                projection_hash=projection_hash,
                schema_version=version.schema_version,
            )
            self._session.add(row)
            await self._session.flush()
            claim_ids[claim.claim_key] = row.id

            for ref in claim.evidence_refs:
                evidence_row = VisualEvidenceRefModel(
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_id=version_row.id,
                    claim_id=row.id,
                    evidence_key=ref.evidence_key,
                    source_snapshot_id=ref.source_snapshot_id,
                    source_snapshot_hash=ref.source_snapshot_hash,
                    chapter_id=ref.chapter_id,
                    chapter_number=ref.chapter_number,
                    source_start=ref.source_start,
                    source_end=ref.source_end,
                    content_hash=ref.content_hash,
                    excerpt=ref.excerpt,
                    cutoff_chapter=ref.cutoff_chapter,
                    idempotency_key=_child_idempotency_key(
                        kind="visual_bible_evidence",
                        owner_id=owner_id,
                        novel_id=novel_id,
                        version_key=version.version_key,
                        child_key=f"{claim.claim_key}:{ref.evidence_key}",
                        payload_hash=ref.content_hash,
                    ),
                )
                self._session.add(evidence_row)
        await self._session.flush()
        return claim_ids

    async def _persist_assets(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version: VisualBibleVersionContract,
        version_row: VisualBibleVersion,
        projection_hash: str,
    ) -> None:
        for asset in version.reference_assets:
            payload = asset.model_dump(mode="json")
            payload_hash = canonical_visual_hash(payload)
            row = VisualReferenceAsset(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version_row.id,
                asset_key=asset.asset_key,
                asset_id=asset.asset_id,
                mime_type=asset.mime_type,
                bytes_hash=asset.bytes_hash,
                rights_status=asset.rights_status.value,
                provenance=asset.provenance,
                approved=False,  # generated/reference assets never silently canon
                canonical_payload=payload,
                canonical_payload_hash=payload_hash,
                idempotency_key=_child_idempotency_key(
                    kind="visual_bible_asset",
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_key=version.version_key,
                    child_key=asset.asset_key,
                    payload_hash=payload_hash,
                ),
                projection_hash=projection_hash,
                schema_version=version.schema_version,
            )
            self._session.add(row)
        await self._session.flush()

    async def _version(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> VisualBibleVersion | None:
        return await self._session.scalar(
            select(VisualBibleVersion).where(
                VisualBibleVersion.owner_id == owner_id,
                VisualBibleVersion.novel_id == novel_id,
                VisualBibleVersion.id == version_id,
            )
        )

    async def _reload_persisted(
        self, version: VisualBibleVersion, *, replayed: bool
    ) -> PersistedRevision:
        entity_ids = {
            row.stable_id: row.id
            for row in (
                await self._session.scalars(
                    select(VisualEntity).where(
                        VisualEntity.owner_id == version.owner_id,
                        VisualEntity.novel_id == version.novel_id,
                        VisualEntity.version_id == version.id,
                    )
                )
            ).all()
        }
        claim_ids = {
            row.claim_key: row.id
            for row in (
                await self._session.scalars(
                    select(VisualClaim).where(
                        VisualClaim.owner_id == version.owner_id,
                        VisualClaim.novel_id == version.novel_id,
                        VisualClaim.version_id == version.id,
                    )
                )
            ).all()
        }
        return PersistedRevision(
            version=version, entity_ids=entity_ids, claim_ids=claim_ids, replayed=replayed
        )

    @staticmethod
    def _require_verified_evidence(
        version: VisualBibleVersionContract,
        verified_evidence: Mapping[str, tuple[VisualEvidenceRef, ...]],
    ) -> None:
        for claim in version.claims:
            if claim.authority is not VisualAuthority.CANON_FACT:
                continue
            provided = verified_evidence.get(claim.claim_key)
            if provided is None:
                raise GateViolationError(
                    f"canon_fact claim {claim.claim_key!r} has no verified evidence"
                )
            provided_keys = {ref.evidence_key for ref in provided}
            expected_keys = {ref.evidence_key for ref in claim.evidence_refs}
            if provided_keys != expected_keys:
                raise GateViolationError(
                    f"canon_fact claim {claim.claim_key!r} verified evidence does not "
                    "match its evidence refs"
                )

    @staticmethod
    def _require_identical_version(
        existing: VisualBibleVersion,
        version: VisualBibleVersionContract,
    ) -> None:
        if (
            existing.canonical_payload_hash != recompute_manifest_hash(version)
            or existing.manifest_hash != version.manifest_hash
            or existing.source_snapshot_hash != version.source_snapshot_hash
            or existing.cutoff_chapter != version.cutoff_chapter
        ):
            raise CandidateConflictError(
                "conflicting candidate retry: version_key already exists with "
                "different immutable content"
            )

    @staticmethod
    def _require_scope(*, owner_id: int, novel_id: int) -> None:
        values = (owner_id, novel_id)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ScopeMismatchError(
                "scope identifiers must be explicit positive integers"
            )


# ---------------------------------------------------------------------------
# Owner-scoped read seams (candidate-only, no unauthorized exposure)
# ---------------------------------------------------------------------------


async def list_versions(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
) -> list[VisualBibleVersionView]:
    """List every candidate revision for the owned novel, oldest first."""
    rows = (
        await session.scalars(
            select(VisualBibleVersion)
            .where(
                VisualBibleVersion.owner_id == owner_id,
                VisualBibleVersion.novel_id == novel_id,
            )
            .order_by(VisualBibleVersion.id.asc())
        )
    ).all()
    return [await load_version_view(session, owner_id=owner_id, novel_id=novel_id, version_id=row.id) for row in rows]


async def load_version_view(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> VisualBibleVersionView:
    """Full candidate envelope for one owned version; 404-equivalent on scope."""
    version = await session.scalar(
        select(VisualBibleVersion).where(
            VisualBibleVersion.owner_id == owner_id,
            VisualBibleVersion.novel_id == novel_id,
            VisualBibleVersion.id == version_id,
        )
    )
    if version is None:
        raise CandidateNotFoundError(
            "visual bible version not found in explicit owner/novel scope"
        )

    entity_rows = (
        await session.scalars(
            select(VisualEntity)
            .where(
                VisualEntity.owner_id == owner_id,
                VisualEntity.novel_id == novel_id,
                VisualEntity.version_id == version_id,
            )
            .order_by(VisualEntity.id.asc())
        )
    ).all()
    claim_rows = (
        await session.scalars(
            select(VisualClaim)
            .where(
                VisualClaim.owner_id == owner_id,
                VisualClaim.novel_id == novel_id,
                VisualClaim.version_id == version_id,
            )
            .order_by(VisualClaim.id.asc())
        )
    ).all()
    evidence_rows = (
        await session.scalars(
            select(VisualEvidenceRefModel)
                .where(
                    VisualEvidenceRefModel.owner_id == owner_id,
                    VisualEvidenceRefModel.novel_id == novel_id,
                    VisualEvidenceRefModel.version_id == version_id,
                )
                .order_by(VisualEvidenceRefModel.id.asc())
        )
    ).all()
    asset_rows = (
        await session.scalars(
            select(VisualReferenceAsset)
            .where(
                VisualReferenceAsset.owner_id == owner_id,
                VisualReferenceAsset.novel_id == novel_id,
                VisualReferenceAsset.version_id == version_id,
            )
            .order_by(VisualReferenceAsset.id.asc())
        )
    ).all()
    review_rows = (
        await session.scalars(
            select(VisualBibleReviewEvent)
            .where(
                VisualBibleReviewEvent.owner_id == owner_id,
                VisualBibleReviewEvent.novel_id == novel_id,
                VisualBibleReviewEvent.version_id == version_id,
            )
            .order_by(VisualBibleReviewEvent.id.asc())
        )
    ).all()

    evidence_by_claim: dict[int, list[VisualEvidenceRefView]] = {}
    for row in evidence_rows:
        evidence_by_claim.setdefault(row.claim_id, []).append(
            VisualEvidenceRefView(
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

    claims_by_entity: dict[str, list[VisualClaimView]] = {}
    for row in claim_rows:
        claims_by_entity.setdefault(row.entity_stable_id, []).append(
            VisualClaimView(
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
        )

    entity_views = [
        VisualEntityView(
            stable_id=row.stable_id,
            entity_key=row.entity_key,
            entity_type=row.entity_type,
            description=row.description,
            authority=row.authority,
            disclosure_cutoff=row.disclosure_cutoff,
            claims=claims_by_entity.get(row.stable_id, []),
        )
        for row in entity_rows
    ]

    return VisualBibleVersionView(
        id=version.id,
        owner_id=version.owner_id,
        novel_id=version.novel_id,
        version_key=version.version_key,
        revision_number=version.revision_number,
        parent_version_id=version.parent_version_id,
        source_snapshot_id=version.source_snapshot_id,
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff_chapter=version.cutoff_chapter,
        schema_version=version.schema_version,
        schema_hash=version.schema_hash,
        policy_hash=version.policy_hash,
        manifest_hash=version.manifest_hash,
        review_state=version.review_state,
        style_profile=version.style_profile,
        constraints=version.constraints,
        entities=entity_views,
        reference_assets=[
            VisualReferenceAssetView(
                asset_key=row.asset_key,
                asset_id=row.asset_id,
                mime_type=row.mime_type,
                bytes_hash=row.bytes_hash,
                rights_status=row.rights_status,
                approved=row.approved,
            )
            for row in asset_rows
        ],
        review_events=[
            VisualReviewEventView(
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
