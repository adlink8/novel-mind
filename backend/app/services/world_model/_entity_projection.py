"""Durable candidate projection, alias-collision detection and pure read engine.

Extracted from ``entities.py`` (refactor split): the sealed
``EntityCandidateProjection`` (D-02) with its checksum / verify / build
helpers, the deterministic ``detect_alias_collisions`` review-candidate
producer (D-04 — similarity only ever yields review candidates, never a silent
merge), and the DB-free ``WorldEntityQueryEngine`` that mirrors the durable
query layer. ``build_entity_candidate`` imports
``provenance.validate_entity_package`` lazily (unchanged) so this module never
feeds a module-level import cycle with the ``entities`` facade.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from pydantic import model_validator

from app.services.world_model.contracts import PositiveInt, StrictModel
from app.services.world_model.rules import RuleException, WorldRule

from ._entity_models import (
    AliasCollisionKind,
    AliasCollisionReview,
    AliasReviewStatus,
    EntityLink,
    EntityType,
    LinkKind,
    WorldEntity,
)
from .entity_primitives import (
    ALIAS_COLLISION_THRESHOLD,
    ENTITY_HASH_IDEM,
    ENTITY_HASH_PROJECTION,
    ENTITY_SCHEMA_VERSION,
    EntityHash64,
    _canonical_json,
    _sha256,
)


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
                (
                    left.primary_name,
                    right.primary_name,
                    AliasCollisionKind.NAME_SIMILARITY,
                )
            )
            for alias in right.aliases:
                candidates.append(
                    (
                        left.primary_name,
                        alias.alias,
                        AliasCollisionKind.ALIAS_SIMILARITY,
                    )
                )
            for alias in left.aliases:
                candidates.append(
                    (
                        alias.alias,
                        right.primary_name,
                        AliasCollisionKind.ALIAS_SIMILARITY,
                    )
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
                disclosure_cutoff=max(left.disclosure_cutoff, right.disclosure_cutoff),
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
        for row in (
            *self.entities,
            *self.links,
            *self.rules,
            *self.exceptions,
            *self.alias_reviews,
        ):
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
                cutoff is None or visible_at_cutoff(exception.disclosure_cutoff, cutoff)
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
