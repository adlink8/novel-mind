"""Immutable typed entity / link / alias / claim models for REQ-WM-03.

Extracted from ``entities.py`` (refactor split): the four typed entity kinds
(``EntityType``), the membership / ownership / spatial / item-state link kinds
(``LinkKind``), the alias lifecycle enums, the durable rows ``EntityAlias`` /
``WorldEntity`` / ``EntityLink`` / ``AliasCollisionReview`` and the gate inputs
``EntityClaim`` / ``EntityLinkClaim``. Depends only on the ``entity_primitives``
leaf (checksums) and the ``world_model`` contract primitives — never on the
``entities`` facade.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.services.world_model.contracts import (
    Authority,
    Description,
    EvidenceRef,
    GateStatus,
    Key,
    PositiveInt,
    StrictModel,
)
from app.services.world_model.rules import SourceKind

from .entity_primitives import (
    ENTITY_HASH_ALIAS_REVIEW,
    ENTITY_HASH_ENTITY,
    ENTITY_HASH_LINK,
    alias_review_checksum,
    entity_checksum,
    link_checksum,
    row_idempotency_key,
)


class EntityType(StrEnum):
    """The four typed entity projections of REQ-WM-03."""

    ENTITY = "entity"
    FACTION = "faction"
    PLACE = "place"
    ITEM = "item"


class LinkKind(StrEnum):
    """Membership, ownership, spatial and item-state links between entities."""

    MEMBER_OF = "member_of"  # membership: character belongs to a faction
    ALLEGIANCE = "allegiance"  # membership: faction/entity allegiance
    CONTROLS = "controls"  # ownership: a faction controls a place
    OWNS = "owns"  # ownership: an entity owns an item
    LOCATED_IN = "located_in"  # spatial: entity/place located inside a place
    CARRIED_BY = "carried_by"  # item state: an item is carried by an entity


class AliasStatus(StrEnum):
    """Lifecycle of one alias on an entity (never auto-merged)."""

    ACTIVE = "active"
    REVIEW = "review"
    REJECTED = "rejected"


class AliasCollisionKind(StrEnum):
    EXACT_ALIAS = "exact_alias"
    NAME_SIMILARITY = "name_similarity"
    ALIAS_SIMILARITY = "alias_similarity"


class AliasReviewStatus(StrEnum):
    """Alias collision reviews are candidates for a human/author, never facts."""

    REVIEW = "review"
    RESOLVED = "resolved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Aliases, links and typed entities
# ---------------------------------------------------------------------------


class EntityAlias(StrictModel):
    """One alias of an entity. ``status`` keeps collisions reviewable."""

    alias: Key
    status: AliasStatus = AliasStatus.ACTIVE


class WorldEntity(StrictModel):
    """Immutable typed entity/faction/place/item candidate (D-03)."""

    claim_kind: str = "world_entity"
    entity_key: Key
    entity_type: EntityType
    primary_name: Key
    description: Description
    aliases: tuple[EntityAlias, ...] = ()
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
    lineage: Annotated[tuple[Key, ...], Field(min_length=1)]
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    gate_status: GateStatus
    gate_reason: Annotated[str, StringConstraints(max_length=120)] | None = None
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt

    @field_validator("source_refs")
    @classmethod
    def _unique_evidence(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        ids = [ref.evidence_id for ref in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source_refs must be unique")
        return value

    @field_validator("aliases")
    @classmethod
    def _unique_aliases(cls, value: tuple[EntityAlias, ...]) -> tuple[EntityAlias, ...]:
        names = [alias.alias for alias in value]
        if len(names) != len(set(names)):
            raise ValueError("aliases must be unique within an entity")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _reject_confidence_coercion(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("confidence must be a JSON number with fractional type")
        return value

    @model_validator(mode="after")
    def _lineage_ends_at_self(self) -> "WorldEntity":
        if not self.lineage or self.lineage[-1] != self.entity_key:
            raise ValueError("lineage must be a version chain ending at this entity")
        return self

    @property
    def checksum(self) -> str:
        return entity_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(ENTITY_HASH_ENTITY, self.model_dump(mode="json"))


class EntityLink(StrictModel):
    """Immutable membership/ownership/spatial/item-state link (D-03).

    ``source_key`` is the subject, ``target_key`` the object. Links are
    evidence-backed and append-only; they never mutate their endpoints.
    """

    claim_kind: str = "entity_link"
    link_key: Key
    link_kind: LinkKind
    source_key: Key
    target_key: Key
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    gate_status: GateStatus
    gate_reason: Annotated[str, StringConstraints(max_length=120)] | None = None
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt

    @model_validator(mode="after")
    def _distinct_endpoints(self) -> "EntityLink":
        if self.source_key == self.target_key:
            raise ValueError("entity link endpoints must differ")
        return self

    @field_validator("source_refs")
    @classmethod
    def _unique_evidence(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        ids = [ref.evidence_id for ref in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source_refs must be unique")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _reject_confidence_coercion(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("confidence must be a JSON number with fractional type")
        return value

    @property
    def checksum(self) -> str:
        return link_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(ENTITY_HASH_LINK, self.model_dump(mode="json"))


class AliasCollisionReview(StrictModel):
    """A review-only alias collision candidate (never a silent merge).

    Produced deterministically from entity name/alias similarity; ``status``
    always starts at ``REVIEW`` and requires a human/author decision. The two
    entities keep their own keys — nothing is merged.
    """

    review_key: Key
    kind: AliasCollisionKind
    entity_key_a: Key
    entity_key_b: Key
    matched_alias: Key
    similarity: Annotated[float, Field(ge=0.0, le=1.0)]
    status: AliasReviewStatus = AliasReviewStatus.REVIEW
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt

    @model_validator(mode="after")
    def _distinct_entities(self) -> "AliasCollisionReview":
        if self.entity_key_a == self.entity_key_b:
            raise ValueError("alias review must pair two distinct entities")
        return self

    @field_validator("source_refs")
    @classmethod
    def _unique_evidence(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        ids = [ref.evidence_id for ref in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source_refs must be unique")
        return value

    @property
    def checksum(self) -> str:
        return alias_review_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(
            ENTITY_HASH_ALIAS_REVIEW, self.model_dump(mode="json")
        )


# ---------------------------------------------------------------------------
# Claims (gate inputs, never persisted)
# ---------------------------------------------------------------------------


class EntityClaim(StrictModel):
    """Gate input proposing one typed entity/faction/place/item."""

    claim_kind: str = "entity"
    entity_key: Key
    entity_type: EntityType
    primary_name: Key
    description: Description
    aliases: tuple[EntityAlias, ...] = ()
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt

    @field_validator("aliases")
    @classmethod
    def _unique_aliases(cls, value: tuple[EntityAlias, ...]) -> tuple[EntityAlias, ...]:
        names = [alias.alias for alias in value]
        if len(names) != len(set(names)):
            raise ValueError("aliases must be unique within an entity")
        return value


class EntityLinkClaim(StrictModel):
    """Gate input proposing one membership/ownership/spatial/item-state link."""

    claim_kind: str = "entity_link"
    link_key: Key
    link_kind: LinkKind
    source_key: Key
    target_key: Key
    source_kind: SourceKind = SourceKind.CANON_SOURCE
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
