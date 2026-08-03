"""Immutable typed contracts and gates for world entities (REQ-WM-03).

Phase 27-03. Typed candidates for entity / faction / place / item, plus the
entity links that express membership (``member_of`` / ``allegiance``),
ownership (``owns`` / ``controls``) and spatial / item state (``located_in`` /
``carried_by``). Semantics locked by decisions D-01..D-06:

- D-01: ``Authority`` keeps the four distinct labels; the gate rejects any
  attempt to silently upgrade an inference or interpretation into ``canon_fact``
  unless explicitly approved.
- D-02: The durable output is a versioned immutable candidate set. There is no
  active-pointer / promotion / current-revision field and no promotion API.
- D-03: Every entity, alias, link, rule, rule exception and alias review carries
  owner/novel/version/cutoff, source EvidenceRefs, authority, confidence and gate
  status. ``lineage`` keeps the version chain of a logical entity.
- D-04: Rule exceptions are first-class records (see ``rules.py``); alias
  collisions are never silently merged. Alias similarity produces only
  ``AliasCollisionReview`` candidates with ``status == REVIEW``.
- D-06: Reader Chat / user conversation is never a world-model fact source; the
  gate rejects such claims on any authority.

Only the immutable candidate projection (``EntityCandidateProjection``) crosses
the persistence seam.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Annotated, Any, Iterable

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
from app.services.world_model.rules import (
    GateReason,
    RuleException,
    SourceKind,
    WorldRule,
    exception_checksum,
    rule_checksum,
)

EntityHash64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

ENTITY_SCHEMA_VERSION = "world-model-entity.v1"
ENTITY_HASH_ENTITY = f"{ENTITY_SCHEMA_VERSION}:entity"
ENTITY_HASH_LINK = f"{ENTITY_SCHEMA_VERSION}:link"
ENTITY_HASH_ALIAS_REVIEW = f"{ENTITY_SCHEMA_VERSION}:alias_review"
ENTITY_HASH_PROJECTION = f"{ENTITY_SCHEMA_VERSION}:projection"
ENTITY_HASH_IDEM = f"{ENTITY_SCHEMA_VERSION}:idem"

ALIAS_COLLISION_THRESHOLD = 0.75

#: Values shared with ``world_model_rules`` source_kind check constraints.
ENTITY_SOURCE_KIND_VALUES = tuple(kind.value for kind in SourceKind)


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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(component: str, body: str) -> str:
    return hashlib.sha256(f"{component}\n{body}".encode("utf-8")).hexdigest()


def entity_checksum(entity: "WorldEntity") -> str:
    return _sha256(
        ENTITY_HASH_ENTITY, _canonical_json(entity.model_dump(mode="json"))
    )


def link_checksum(link: "EntityLink") -> str:
    return _sha256(ENTITY_HASH_LINK, _canonical_json(link.model_dump(mode="json")))


def alias_review_checksum(review: "AliasCollisionReview") -> str:
    return _sha256(
        ENTITY_HASH_ALIAS_REVIEW, _canonical_json(review.model_dump(mode="json"))
    )


def row_idempotency_key(component: str, payload: dict[str, Any]) -> str:
    """Deterministic replay key over one row's canonical payload."""
    return _sha256(ENTITY_HASH_IDEM, _canonical_json(payload))


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
    def _unique_aliases(
        cls, value: tuple[EntityAlias, ...]
    ) -> tuple[EntityAlias, ...]:
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
        return row_idempotency_key(
            ENTITY_HASH_ENTITY, self.model_dump(mode="json")
        )


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
    def _unique_aliases(
        cls, value: tuple[EntityAlias, ...]
    ) -> tuple[EntityAlias, ...]:
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


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityVerdict:
    passed: bool
    reason_code: GateReason
    message: str


@dataclass(frozen=True)
class EntityGateResult:
    entity: WorldEntity | None
    verdicts: tuple[EntityVerdict, ...]

    @property
    def reason_codes(self) -> frozenset[GateReason]:
        return frozenset(verdict.reason_code for verdict in self.verdicts)


@dataclass(frozen=True)
class EntityLinkGateResult:
    link: EntityLink | None
    verdicts: tuple[EntityVerdict, ...]

    @property
    def reason_codes(self) -> frozenset[GateReason]:
        return frozenset(verdict.reason_code for verdict in self.verdicts)


class EntityGate:
    """Scope-locked, fail-closed gate for one entity/link submission."""

    def __init__(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        source_snapshot_hash: str,
        disclosure_cutoff: int,
        approvals: frozenset[Authority] = frozenset(),
    ) -> None:
        self.owner_id = owner_id
        self.novel_id = novel_id
        self.version_id = version_id
        self.source_snapshot_hash = source_snapshot_hash
        self.disclosure_cutoff = disclosure_cutoff
        self.approvals = approvals

    def _base_verdicts(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        source_kind: SourceKind,
        authority: Authority,
        disclosure_cutoff: int,
        source_refs: tuple[EvidenceRef, ...],
    ) -> list[EntityVerdict]:
        verdicts: list[EntityVerdict] = []
        if owner_id != self.owner_id or novel_id != self.novel_id:
            verdicts.append(
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.WRONG_OWNER,
                    message=(
                        f"claim scope {owner_id}/{novel_id} does not match gate "
                        f"scope {self.owner_id}/{self.novel_id}"
                    ),
                )
            )
            return verdicts
        if version_id != self.version_id:
            verdicts.append(
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.STALE_VERSION,
                    message=(
                        f"claim version {version_id} is not the gated version "
                        f"{self.version_id}"
                    ),
                )
            )
            return verdicts

        if not source_refs:
            verdicts.append(
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.NO_EVIDENCE,
                    message="entity/link requires at least one evidence ref",
                )
            )
        for ref in source_refs:
            if ref.source_snapshot_hash != self.source_snapshot_hash:
                verdicts.append(
                    EntityVerdict(
                        passed=False,
                        reason_code=GateReason.STALE_EVIDENCE,
                        message=(
                            f"evidence {ref.evidence_id} is stale: snapshot "
                            f"{ref.source_snapshot_hash[:8]}… does not match the "
                            f"frozen source package {self.source_snapshot_hash[:8]}…"
                        ),
                    )
                )
                break

        if disclosure_cutoff > self.disclosure_cutoff:
            verdicts.append(
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.SPOILER_CUTOFF,
                    message=(
                        f"disclosure cutoff {disclosure_cutoff} is beyond the "
                        f"authorized cutoff {self.disclosure_cutoff}"
                    ),
                )
            )
        for ref in source_refs:
            if ref.chapter_number > disclosure_cutoff:
                verdicts.append(
                    EntityVerdict(
                        passed=False,
                        reason_code=GateReason.EVIDENCE_BEYOND_CUTOFF,
                        message=(
                            f"evidence {ref.evidence_id} is at chapter "
                            f"{ref.chapter_number}, after the claim cutoff "
                            f"{disclosure_cutoff}"
                        ),
                    )
                )
                break

        # D-06: Reader Chat / user conversation is never a fact source.
        if source_kind in {
            SourceKind.READER_CHAT,
            SourceKind.USER_CONVERSATION,
        }:
            verdicts.append(
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.CHAT_NOT_FACT_SOURCE,
                    message=(
                        "Reader Chat / user conversation is never a world-model "
                        "fact source (D-06)"
                    ),
                )
            )

        if authority == Authority.CANON_FACT and Authority.CANON_FACT not in self.approvals:
            verdicts.append(
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.AUTHORITY_UPGRADE,
                    message=(
                        "canon_fact requires explicit approval; inference / "
                        "interpretation must never serialize as canon_fact (D-01)"
                    ),
                )
            )
        if (
            authority == Authority.USER_INTERPRETATION
            and Authority.USER_INTERPRETATION not in self.approvals
        ):
            verdicts.append(
                EntityVerdict(
                    passed=False,
                    reason_code=GateReason.MISSING_APPROVAL,
                    message=(
                        "user_interpretation requires explicit confirmation (D-06)"
                    ),
                )
            )
        return verdicts

    def validate_entity(self, claim: EntityClaim) -> EntityGateResult:
        verdicts = self._base_verdicts(
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
            source_kind=claim.source_kind,
            authority=claim.authority,
            disclosure_cutoff=claim.disclosure_cutoff,
            source_refs=claim.source_refs,
        )
        if any(not verdict.passed for verdict in verdicts):
            return EntityGateResult(None, tuple(verdicts))
        verdicts.append(
            EntityVerdict(
                passed=True,
                reason_code=GateReason.GATE_PASSED,
                message="entity gate passed",
            )
        )
        entity = WorldEntity(
            entity_key=claim.entity_key,
            entity_type=claim.entity_type,
            primary_name=claim.primary_name,
            description=claim.description,
            aliases=claim.aliases,
            source_kind=claim.source_kind,
            authority=claim.authority,
            confidence=claim.confidence,
            disclosure_cutoff=claim.disclosure_cutoff,
            lineage=(claim.entity_key,),
            source_refs=claim.source_refs,
            gate_status=GateStatus.PASSED,
            gate_reason=None,
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
        )
        return EntityGateResult(entity, tuple(verdicts))

    def validate_link(self, claim: EntityLinkClaim) -> EntityLinkGateResult:
        verdicts = self._base_verdicts(
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
            source_kind=claim.source_kind,
            authority=claim.authority,
            disclosure_cutoff=claim.disclosure_cutoff,
            source_refs=claim.source_refs,
        )
        if any(not verdict.passed for verdict in verdicts):
            return EntityLinkGateResult(None, tuple(verdicts))
        verdicts.append(
            EntityVerdict(
                passed=True,
                reason_code=GateReason.GATE_PASSED,
                message="entity link gate passed",
            )
        )
        link = EntityLink(
            link_key=claim.link_key,
            link_kind=claim.link_kind,
            source_key=claim.source_key,
            target_key=claim.target_key,
            source_kind=claim.source_kind,
            authority=claim.authority,
            confidence=claim.confidence,
            disclosure_cutoff=claim.disclosure_cutoff,
            source_refs=claim.source_refs,
            gate_status=GateStatus.PASSED,
            gate_reason=None,
            owner_id=claim.owner_id,
            novel_id=claim.novel_id,
            version_id=claim.version_id,
        )
        return EntityLinkGateResult(link, tuple(verdicts))


# ---------------------------------------------------------------------------
# Alias collision detection — review candidates only, never a silent merge
# ---------------------------------------------------------------------------


def _normalize_name(text: str) -> str:
    """Casefold and strip common punctuation/spacing for fuzzy comparison."""
    return re.sub(r"[\s，。、,.;；:：'\"“”‘’!！?？\-_—…]+", "", text.casefold())


def name_similarity(left: str, right: str) -> float:
    """Normalized fuzzy similarity in [0, 1]; equal names score exactly 1.0."""
    a, b = _normalize_name(left), _normalize_name(right)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def detect_alias_collisions(
    entities: Iterable[WorldEntity],
    *,
    threshold: float = ALIAS_COLLISION_THRESHOLD,
) -> tuple[AliasCollisionReview, ...]:
    """Compare primary names and aliases across entities.

    Produces only ``AliasCollisionReview`` candidates with ``status == REVIEW``;
    no entity is ever merged, renamed or removed. For each pair of distinct
    entities the single highest-similarity match is reported (the review key is
    deterministic so replay is byte-stable).
    """
    ordered = sorted(entities, key=lambda entity: entity.entity_key)
    reviews: list[AliasCollisionReview] = []
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            if (
                left.owner_id != right.owner_id
                or left.novel_id != right.novel_id
                or left.version_id != right.version_id
            ):
                continue
            best_similarity = 0.0
            best_label = ""
            best_kind = AliasCollisionKind.ALIAS_SIMILARITY
            candidates: list[tuple[str, str, AliasCollisionKind]] = []
            candidates.append(
                (left.primary_name, right.primary_name, AliasCollisionKind.NAME_SIMILARITY)
            )
            for alias in right.aliases:
                candidates.append(
                    (left.primary_name, alias.alias, AliasCollisionKind.ALIAS_SIMILARITY)
                )
            for alias in left.aliases:
                candidates.append(
                    (alias.alias, right.primary_name, AliasCollisionKind.ALIAS_SIMILARITY)
                )
                for other in right.aliases:
                    candidates.append(
                        (alias.alias, other.alias, AliasCollisionKind.EXACT_ALIAS)
                    )
            for a, b, kind in candidates:
                score = name_similarity(a, b)
                if score > best_similarity:
                    best_similarity = score
                    best_label = a if score >= threshold else best_label
                    best_kind = kind
            if best_similarity < threshold:
                continue
            a_key, b_key = sorted((left.entity_key, right.entity_key))
            review = AliasCollisionReview(
                review_key=f"alias-review:{a_key}:{b_key}",
                kind=best_kind,
                entity_key_a=a_key,
                entity_key_b=b_key,
                matched_alias=best_label or a_key,
                similarity=best_similarity,
                status=AliasReviewStatus.REVIEW,
                disclosure_cutoff=max(
                    left.disclosure_cutoff, right.disclosure_cutoff
                ),
                source_refs=left.source_refs,
                owner_id=left.owner_id,
                novel_id=left.novel_id,
                version_id=left.version_id,
            )
            reviews.append(review)
    return tuple(reviews)


# ---------------------------------------------------------------------------
# Durable candidate projection
# ---------------------------------------------------------------------------


class EntityCandidateProjection(StrictModel):
    """The only durable output (D-02): a versioned immutable world projection.

    Combines typed entities/factions/places/items, membership/ownership/spatial
    links, world rules, first-class rule exceptions and review-only alias
    collisions. ``projection_hash`` is recomputed on every read so byte-drifted
    rows fail closed.
    """

    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    schema_version: str = ENTITY_SCHEMA_VERSION
    entities: tuple[WorldEntity, ...] = ()
    links: tuple[EntityLink, ...] = ()
    rules: tuple[WorldRule, ...] = ()
    exceptions: tuple[RuleException, ...] = ()
    alias_reviews: tuple[AliasCollisionReview, ...] = ()
    projection_hash: EntityHash64 = "0" * 64

    @model_validator(mode="after")
    def _scope_matches_rows(self) -> "EntityCandidateProjection":
        for row in (*self.entities, *self.links, *self.rules, *self.exceptions, *self.alias_reviews):
            if (
                row.owner_id != self.owner_id
                or row.novel_id != self.novel_id
                or row.version_id != self.version_id
            ):
                raise ValueError("row scope must match the projection scope")
        entity_keys = {entity.entity_key for entity in self.entities}
        for link in self.links:
            if link.source_key not in entity_keys or link.target_key not in entity_keys:
                raise ValueError("link endpoints must be projection-local entities")
        rule_keys = {rule.rule_key for rule in self.rules}
        for exception in self.exceptions:
            if exception.rule_key not in rule_keys:
                raise ValueError(
                    f"exception '{exception.exception_key}' references unknown "
                    f"rule '{exception.rule_key}'"
                )
            if (
                exception.applies_to is not None
                and exception.applies_to not in entity_keys
            ):
                raise ValueError(
                    f"exception '{exception.exception_key}' applies_to "
                    f"'{exception.applies_to}' is not a projection-local entity"
                )
        for review in self.alias_reviews:
            if (
                review.entity_key_a not in entity_keys
                or review.entity_key_b not in entity_keys
            ):
                raise ValueError(
                    "alias review endpoints must be projection-local entities"
                )
        return self

    @property
    def idempotency_key(self) -> str:
        body = _canonical_json(
            {
                "owner_id": self.owner_id,
                "novel_id": self.novel_id,
                "version_id": self.version_id,
                "entities": [e.model_dump(mode="json") for e in self.entities],
                "links": [link.model_dump(mode="json") for link in self.links],
                "rules": [r.model_dump(mode="json") for r in self.rules],
                "exceptions": [x.model_dump(mode="json") for x in self.exceptions],
                "alias_reviews": [
                    r.model_dump(mode="json") for r in self.alias_reviews
                ],
            }
        )
        return _sha256(ENTITY_HASH_IDEM, body)


def entity_projection_checksum(projection: EntityCandidateProjection) -> str:
    body = _canonical_json(
        {
            "owner_id": projection.owner_id,
            "novel_id": projection.novel_id,
            "version_id": projection.version_id,
            "entities": [e.model_dump(mode="json") for e in projection.entities],
            "links": [link.model_dump(mode="json") for link in projection.links],
            "rules": [r.model_dump(mode="json") for r in projection.rules],
            "exceptions": [x.model_dump(mode="json") for x in projection.exceptions],
            "alias_reviews": [
                r.model_dump(mode="json") for r in projection.alias_reviews
            ],
        }
    )
    return _sha256(ENTITY_HASH_PROJECTION, body)


def build_entity_projection(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    entities: list[WorldEntity] | tuple[WorldEntity, ...] = (),
    links: list[EntityLink] | tuple[EntityLink, ...] = (),
    rules: list[WorldRule] | tuple[WorldRule, ...] = (),
    exceptions: list[RuleException] | tuple[RuleException, ...] = (),
    alias_reviews: list[AliasCollisionReview] | tuple[AliasCollisionReview, ...] = (),
) -> EntityCandidateProjection:
    """Construct the immutable candidate with a sealed projection hash."""
    projection = EntityCandidateProjection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        entities=tuple(entities),
        links=tuple(links),
        rules=tuple(rules),
        exceptions=tuple(exceptions),
        alias_reviews=tuple(alias_reviews),
        projection_hash="0" * 64,
    )
    checksum = entity_projection_checksum(projection)
    return projection.model_copy(update={"projection_hash": checksum})


def entity_projection_verified(projection: EntityCandidateProjection) -> bool:
    """Recompute and compare the sealed projection hash (byte-equivalence)."""
    return entity_projection_checksum(projection) == projection.projection_hash


def build_entity_candidate(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    entities: list[WorldEntity] | tuple[WorldEntity, ...] = (),
    links: list[EntityLink] | tuple[EntityLink, ...] = (),
    rules: list[WorldRule] | tuple[WorldRule, ...] = (),
    exceptions: list[RuleException] | tuple[RuleException, ...] = (),
) -> EntityCandidateProjection:
    """Gate-blessed immutable candidate with alias reviews and provenance checks.

    Alias similarity always produces review candidates and never merges
    entities; structural provenance is validated fail-closed before the sealed
    projection is built.
    """
    from app.services.world_model.provenance import validate_entity_package

    result = validate_entity_package(
        entities=list(entities),
        links=list(links),
        rules=list(rules),
        exceptions=list(exceptions),
    )
    if not result.ok:
        raise ValueError(
            f"entity package provenance failed: {','.join(result.reason_codes)}"
        )
    reviews = detect_alias_collisions(entities)
    return build_entity_projection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        entities=entities,
        links=links,
        rules=rules,
        exceptions=exceptions,
        alias_reviews=list(reviews),
    )


# ---------------------------------------------------------------------------
# Pure read-only query engine (DB-free; mirrors the durable query layer)
# ---------------------------------------------------------------------------


def visible_at_cutoff(disclosure_cutoff: int, cutoff: int) -> bool:
    """D-05 disclosure helper: only rows disclosed at/before ``cutoff``."""
    return disclosure_cutoff <= cutoff


class WorldEntityQueryEngine:
    """Pure in-memory, owner-scoped query API over one immutable projection."""

    def __init__(self, projection: EntityCandidateProjection) -> None:
        self._projection = projection

    def query_entities(
        self,
        *,
        entity_type: EntityType | None = None,
        cutoff: int | None = None,
    ) -> tuple[WorldEntity, ...]:
        rows = [
            entity
            for entity in self._projection.entities
            if (entity_type is None or entity.entity_type == entity_type)
            and (cutoff is None or visible_at_cutoff(entity.disclosure_cutoff, cutoff))
        ]
        rows.sort(key=lambda entity: entity.entity_key)
        return tuple(rows)

    def query_links(
        self,
        *,
        link_kind: LinkKind | None = None,
        cutoff: int | None = None,
    ) -> tuple[EntityLink, ...]:
        rows = [
            link
            for link in self._projection.links
            if (link_kind is None or link.link_kind == link_kind)
            and (cutoff is None or visible_at_cutoff(link.disclosure_cutoff, cutoff))
        ]
        rows.sort(key=lambda link: link.link_key)
        return tuple(rows)

    def query_rules(self, *, cutoff: int | None = None) -> tuple[WorldRule, ...]:
        rows = [
            rule
            for rule in self._projection.rules
            if cutoff is None or visible_at_cutoff(rule.disclosure_cutoff, cutoff)
        ]
        rows.sort(key=lambda rule: rule.rule_key)
        return tuple(rows)

    def query_exceptions(
        self,
        *,
        rule_key: str | None = None,
        cutoff: int | None = None,
    ) -> tuple[RuleException, ...]:
        rows = [
            exception
            for exception in self._projection.exceptions
            if (rule_key is None or exception.rule_key == rule_key)
            and (
                cutoff is None
                or visible_at_cutoff(exception.disclosure_cutoff, cutoff)
            )
        ]
        rows.sort(key=lambda exception: exception.exception_key)
        return tuple(rows)

    def query_alias_reviews(
        self, *, cutoff: int | None = None
    ) -> tuple[AliasCollisionReview, ...]:
        rows = [
            review
            for review in self._projection.alias_reviews
            if cutoff is None or visible_at_cutoff(review.disclosure_cutoff, cutoff)
        ]
        rows.sort(key=lambda review: review.review_key)
        return tuple(rows)

    def query_entity_lineage(self, entity_key: str) -> tuple[WorldEntity, ...]:
        rows = [
            entity
            for entity in self._projection.entities
            if entity.entity_key == entity_key or entity_key in entity.lineage
        ]
        rows.sort(key=lambda entity: (entity.version_id, entity.entity_key))
        return tuple(rows)

    def query_rule_lineage(self, rule_key: str) -> tuple[WorldRule, ...]:
        rows = [
            rule
            for rule in self._projection.rules
            if rule.rule_key == rule_key or rule_key in rule.lineage
        ]
        rows.sort(key=lambda rule: (rule.version_id, rule.rule_key))
        return tuple(rows)


# ---------------------------------------------------------------------------
# Re-export the rule-side checksum helpers for the repository layer.
# ---------------------------------------------------------------------------

__all__ = [
    "ALIAS_COLLISION_THRESHOLD",
    "AliasCollisionKind",
    "AliasCollisionReview",
    "AliasReviewStatus",
    "AliasStatus",
    "EntityAlias",
    "EntityCandidateProjection",
    "EntityClaim",
    "EntityGate",
    "EntityGateResult",
    "EntityLink",
    "EntityLinkClaim",
    "EntityLinkGateResult",
    "EntityType",
    "ENTITY_SCHEMA_VERSION",
    "LinkKind",
    "SourceKind",
    "WorldEntity",
    "WorldEntityQueryEngine",
    "alias_review_checksum",
    "build_entity_candidate",
    "build_entity_projection",
    "detect_alias_collisions",
    "entity_checksum",
    "entity_projection_checksum",
    "entity_projection_verified",
    "exception_checksum",
    "link_checksum",
    "name_similarity",
    "rule_checksum",
    "visible_at_cutoff",
]
