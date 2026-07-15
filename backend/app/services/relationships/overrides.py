"""
Append-only protective relationship overrides (D-17 / D-18).

Supersession is always a new INSERT. Prior rows remain byte-stable because
PostgreSQL triggers reject UPDATE/DELETE on override tables.
Cross-version relink requires exactly one stable evidence signature match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.character import Character
from app.models.relationship import (
    CharacterIdentityOverride,
    RelationshipObservation,
    RelationshipOverride,
)
from app.schemas.relationship import (
    CharacterIdentityOverrideCreate,
    CharacterIdentityOverrideResponse,
    OverrideStatus,
    RelationshipOverrideCreate,
    RelationshipOverrideField,
    RelationshipOverrideResponse,
)
from app.services.relationships.query import (
    RelationshipGraphQueryService,
    logical_relationship_key,
)


class OverrideValidationError(ValueError):
    """Raised when override endpoints or evidence fail server-side proof."""


class OverrideNotFoundError(LookupError):
    """Raised when scoped version or target observation is not accessible."""


@dataclass(frozen=True)
class RelinkOutcome:
    status: str
    matched_observation_id: int | None
    match_count: int


class RelationshipOverrideService:
    """Insert-only character merge and relationship field corrections."""

    def __init__(self, query_service: RelationshipGraphQueryService | None = None) -> None:
        self._query = query_service or RelationshipGraphQueryService()

    async def append_character_merge(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        payload: CharacterIdentityOverrideCreate,
    ) -> CharacterIdentityOverrideResponse:
        await self._prove_version(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=payload.analysis_version_id,
        )
        await self._prove_characters(
            session,
            novel_id=novel_id,
            character_ids=[payload.canonical_character_id, *payload.merged_character_ids],
        )

        prior = await session.scalar(
            select(CharacterIdentityOverride)
            .where(
                CharacterIdentityOverride.owner_id == owner_id,
                CharacterIdentityOverride.novel_id == novel_id,
                CharacterIdentityOverride.analysis_version_id
                == payload.analysis_version_id,
                CharacterIdentityOverride.canonical_character_id
                == payload.canonical_character_id,
                CharacterIdentityOverride.status == OverrideStatus.ACTIVE.value,
            )
            .order_by(CharacterIdentityOverride.id.desc())
            .limit(1)
        )

        row = CharacterIdentityOverride(
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=payload.analysis_version_id,
            canonical_character_id=payload.canonical_character_id,
            merged_character_ids=list(payload.merged_character_ids),
            author=payload.author,
            reason=payload.reason,
            evidence_signature=payload.evidence_signature,
            supersedes_id=payload.supersedes_id or (prior.id if prior else None),
            status=payload.status.value
            if isinstance(payload.status, OverrideStatus)
            else str(payload.status),
            provenance=dict(payload.provenance or {}),
        )
        session.add(row)
        await session.flush()
        return self._identity_response(row)

    async def append_relationship_override(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        payload: RelationshipOverrideCreate,
    ) -> RelationshipOverrideResponse:
        await self._prove_version(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=payload.analysis_version_id,
        )

        observation: RelationshipObservation | None = None
        if payload.observation_id is not None:
            observation = await session.scalar(
                select(RelationshipObservation).where(
                    RelationshipObservation.id == payload.observation_id,
                    RelationshipObservation.owner_id == owner_id,
                    RelationshipObservation.novel_id == novel_id,
                    RelationshipObservation.analysis_version_id
                    == payload.analysis_version_id,
                    RelationshipObservation.status == "accepted",
                )
            )
            if observation is None:
                raise OverrideNotFoundError("observation not found in version scope")

        field_name = (
            payload.field_name.value
            if isinstance(payload.field_name, RelationshipOverrideField)
            else str(payload.field_name)
        )
        if field_name not in {
            f.value for f in RelationshipOverrideField
        }:
            raise OverrideValidationError(f"unsupported override field: {field_name}")

        self._validate_field_value(field_name, payload.value)

        prior = await session.scalar(
            select(RelationshipOverride)
            .where(
                RelationshipOverride.owner_id == owner_id,
                RelationshipOverride.novel_id == novel_id,
                RelationshipOverride.analysis_version_id == payload.analysis_version_id,
                RelationshipOverride.logical_relationship_key
                == payload.logical_relationship_key,
                RelationshipOverride.field_name == field_name,
            )
            .order_by(RelationshipOverride.id.desc())
            .limit(1)
        )
        # Latest-wins supersession: new active row points at prior id when present.
        supersedes_id = payload.supersedes_id
        if supersedes_id is None and prior is not None:
            supersedes_id = prior.id

        row = RelationshipOverride(
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=payload.analysis_version_id,
            observation_id=payload.observation_id,
            logical_relationship_key=payload.logical_relationship_key,
            field_name=field_name,
            value=dict(payload.value),
            author=payload.author,
            reason=payload.reason,
            evidence_signature=payload.evidence_signature,
            supersedes_id=supersedes_id,
            status=payload.status.value
            if isinstance(payload.status, OverrideStatus)
            else str(payload.status),
            provenance=dict(payload.provenance or {}),
        )
        session.add(row)
        await session.flush()
        return self._override_response(row)

    async def relink_override_to_version(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        override_id: int,
        target_version_id: int,
        override_kind: str = "relationship",
    ) -> CharacterIdentityOverrideResponse | RelationshipOverrideResponse:
        """Cross-version relink: unique evidence signature → active, else needs_relink."""

        await self._prove_version(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=target_version_id,
        )

        if override_kind == "identity":
            prior = await session.scalar(
                select(CharacterIdentityOverride).where(
                    CharacterIdentityOverride.id == override_id,
                    CharacterIdentityOverride.owner_id == owner_id,
                    CharacterIdentityOverride.novel_id == novel_id,
                )
            )
            if prior is None:
                raise OverrideNotFoundError("identity override not found")
            # Identity relink is signature-scoped within target version merges only.
            matches = list(
                (
                    await session.scalars(
                        select(CharacterIdentityOverride).where(
                            CharacterIdentityOverride.owner_id == owner_id,
                            CharacterIdentityOverride.novel_id == novel_id,
                            CharacterIdentityOverride.analysis_version_id
                            == target_version_id,
                            CharacterIdentityOverride.evidence_signature
                            == prior.evidence_signature,
                        )
                    )
                ).all()
            )
            if len(matches) == 1:
                status = OverrideStatus.ACTIVE.value
            else:
                status = OverrideStatus.NEEDS_RELINK.value
            row = CharacterIdentityOverride(
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=target_version_id,
                canonical_character_id=prior.canonical_character_id,
                merged_character_ids=list(prior.merged_character_ids or []),
                author=prior.author,
                reason=f"relink from override {prior.id}",
                evidence_signature=prior.evidence_signature,
                supersedes_id=prior.id,
                status=status,
                provenance={
                    **(prior.provenance or {}),
                    "relinked_from": prior.id,
                    "match_count": len(matches),
                },
            )
            session.add(row)
            await session.flush()
            return self._identity_response(row)

        prior = await session.scalar(
            select(RelationshipOverride).where(
                RelationshipOverride.id == override_id,
                RelationshipOverride.owner_id == owner_id,
                RelationshipOverride.novel_id == novel_id,
            )
        )
        if prior is None:
            raise OverrideNotFoundError("relationship override not found")

        outcome = await self._match_evidence_signature(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=target_version_id,
            evidence_signature=prior.evidence_signature,
        )
        status = (
            OverrideStatus.ACTIVE.value
            if outcome.match_count == 1
            else OverrideStatus.NEEDS_RELINK.value
        )
        row = RelationshipOverride(
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=target_version_id,
            observation_id=outcome.matched_observation_id,
            logical_relationship_key=prior.logical_relationship_key,
            field_name=prior.field_name,
            value=dict(prior.value or {}),
            author=prior.author,
            reason=f"relink from override {prior.id}",
            evidence_signature=prior.evidence_signature,
            supersedes_id=prior.id,
            status=status,
            provenance={
                **(prior.provenance or {}),
                "relinked_from": prior.id,
                "match_count": outcome.match_count,
            },
        )
        session.add(row)
        await session.flush()
        return self._override_response(row)

    async def _match_evidence_signature(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        evidence_signature: str,
    ) -> RelinkOutcome:
        """Match observations whose evidence_checksum equals the signature.

        D-18: zero or multiple matches → needs_relink; unique → active.
        """
        # Signature may equal evidence_checksum or be a free-form stable key
        # stored on the override; prefer exact evidence_checksum match.
        rows = list(
            (
                await session.scalars(
                    select(RelationshipObservation).where(
                        RelationshipObservation.owner_id == owner_id,
                        RelationshipObservation.novel_id == novel_id,
                        RelationshipObservation.analysis_version_id == version_id,
                        RelationshipObservation.status == "accepted",
                        RelationshipObservation.evidence_checksum == evidence_signature,
                    )
                )
            ).all()
        )
        if not rows:
            # Fallback: also allow overrides that already target this version
            # with the same signature (for re-relink idempotency).
            existing = list(
                (
                    await session.scalars(
                        select(RelationshipOverride).where(
                            RelationshipOverride.owner_id == owner_id,
                            RelationshipOverride.novel_id == novel_id,
                            RelationshipOverride.analysis_version_id == version_id,
                            RelationshipOverride.evidence_signature == evidence_signature,
                            RelationshipOverride.status == OverrideStatus.ACTIVE.value,
                        )
                    )
                ).all()
            )
            if len(existing) == 1 and existing[0].observation_id is not None:
                return RelinkOutcome(
                    status=OverrideStatus.ACTIVE.value,
                    matched_observation_id=existing[0].observation_id,
                    match_count=1,
                )
            return RelinkOutcome(
                status=OverrideStatus.NEEDS_RELINK.value,
                matched_observation_id=None,
                match_count=0,
            )
        if len(rows) == 1:
            return RelinkOutcome(
                status=OverrideStatus.ACTIVE.value,
                matched_observation_id=rows[0].id,
                match_count=1,
            )
        return RelinkOutcome(
            status=OverrideStatus.NEEDS_RELINK.value,
            matched_observation_id=None,
            match_count=len(rows),
        )

    async def _prove_version(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
    ) -> None:
        resolved = await self._query.resolve_version(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
        )
        if resolved is None:
            raise OverrideNotFoundError("version not found")

    async def _prove_characters(
        self,
        session: AsyncSession,
        *,
        novel_id: int,
        character_ids: list[int],
    ) -> None:
        rows = list(
            (
                await session.scalars(
                    select(Character).where(
                        Character.novel_id == novel_id,
                        Character.id.in_(character_ids),
                    )
                )
            ).all()
        )
        found = {row.id for row in rows}
        missing = [cid for cid in character_ids if cid not in found]
        if missing:
            raise OverrideValidationError(f"characters out of novel scope: {missing}")

    @staticmethod
    def _validate_field_value(field_name: str, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise OverrideValidationError("override value must be an object")
        if field_name == "relation_type":
            rel = value.get("relation_type")
            if rel not in {"ally", "enemy", "family", "mentor", "romantic"}:
                raise OverrideValidationError("invalid relation_type override")
        elif field_name == "transition":
            tr = value.get("transition")
            if tr not in {"establish", "change", "end"}:
                raise OverrideValidationError("invalid transition override")
        elif field_name == "valid_from":
            if "valid_from_chapter" not in value:
                raise OverrideValidationError("valid_from requires valid_from_chapter")
        elif field_name == "valid_to":
            if "valid_to_chapter" not in value:
                raise OverrideValidationError("valid_to requires valid_to_chapter key")

    @staticmethod
    def _identity_response(row: CharacterIdentityOverride) -> CharacterIdentityOverrideResponse:
        return CharacterIdentityOverrideResponse(
            id=row.id,
            novel_id=row.novel_id,
            analysis_version_id=row.analysis_version_id,
            canonical_character_id=row.canonical_character_id,
            merged_character_ids=list(row.merged_character_ids or []),
            author=row.author,
            reason=row.reason,
            evidence_signature=row.evidence_signature,
            supersedes_id=row.supersedes_id,
            status=OverrideStatus(row.status),
            provenance=dict(row.provenance or {}),
            created_at=row.created_at,
        )

    @staticmethod
    def _override_response(row: RelationshipOverride) -> RelationshipOverrideResponse:
        return RelationshipOverrideResponse(
            id=row.id,
            novel_id=row.novel_id,
            analysis_version_id=row.analysis_version_id,
            observation_id=row.observation_id,
            logical_relationship_key=row.logical_relationship_key,
            field_name=RelationshipOverrideField(row.field_name),
            value=dict(row.value or {}),
            author=row.author,
            reason=row.reason,
            evidence_signature=row.evidence_signature,
            supersedes_id=row.supersedes_id,
            status=OverrideStatus(row.status),
            provenance=dict(row.provenance or {}),
            created_at=row.created_at,
        )


relationship_override_service = RelationshipOverrideService()

__all__ = [
    "OverrideNotFoundError",
    "OverrideValidationError",
    "RelationshipOverrideService",
    "RelinkOutcome",
    "logical_relationship_key",
    "relationship_override_service",
]
