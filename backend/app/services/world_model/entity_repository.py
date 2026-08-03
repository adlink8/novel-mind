"""Append-only durable repository for world entity projections (REQ-WM-03).

Phase 27-03. Semantics (D-02, D-03, D-04):

- ``append_projection`` persists one immutable ``EntityCandidateProjection``
  after the deterministic gates. A unique ``idempotency_key`` conflict only
  replays the existing row; it never creates a second row.
- ``replay_projection`` reconstructs the projection from the five durable
  tables (entities / rules / exceptions / links / alias reviews), recomputes
  every canonical checksum and the sealed ``projection_hash``, and fails closed
  on byte drift (restart replay proof). Rule exceptions and alias collision
  reviews survive replay intact — they are first-class rows, never normalized
  away.
- No UPDATE / DELETE / promote path exists. Cross-owner reads fail closed.
- Stale-version writes (older than the newest stored version) are rejected so
  version lineage stays append-only and monotonic.
- D-06: no row may carry a Reader Chat / user-conversation source kind; a
  replayed row that somehow did would fail the gate check.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.world_model_entity import (
    WorldModelAliasReview,
    WorldModelEntity,
    WorldModelEntityLink,
    WorldModelRule,
    WorldModelRuleException,
)
from app.services.world_model.entities import (
    ENTITY_SCHEMA_VERSION,
    AliasCollisionReview,
    EntityCandidateProjection,
    EntityLink,
    WorldEntity,
    alias_review_checksum,
    entity_checksum,
    entity_projection_checksum,
    link_checksum,
)
from app.services.world_model.rules import (
    CHAT_SOURCE_KINDS,
    RuleException,
    WorldRule,
    exception_checksum,
    rule_checksum,
)

WORLD_MODEL_ENTITY_TABLES = (
    WorldModelEntity,
    WorldModelRule,
    WorldModelRuleException,
    WorldModelEntityLink,
    WorldModelAliasReview,
)


class WorldEntityRepositoryError(ValueError):
    pass


class WorldEntityRepository:
    """Append-only repository. Reads are owner-scoped and checksum-verified."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ append

    async def append_projection(
        self, projection: EntityCandidateProjection
    ) -> None:
        """Persist one immutable projection; idempotent on key conflicts."""

        if projection.projection_hash != entity_projection_checksum(projection):
            raise WorldEntityRepositoryError("projection hash is not sealed")

        await self._assert_version_not_stale(projection)
        for row in self._to_rows(projection):
            self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            # A concurrent unique-key winner only replays identical rows.
            await self._session.rollback()
            await self._replay_existing_or_fail(projection)

    async def replay_projection(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> EntityCandidateProjection:
        """Reconstruct the immutable projection; fail closed on checksum drift."""

        entities = await self._rows(WorldModelEntity, owner_id, novel_id, version_id)
        if not entities:
            raise WorldEntityRepositoryError(
                "projection not found in owner/novel/version scope"
            )
        rules = await self._rows(WorldModelRule, owner_id, novel_id, version_id)
        exceptions = await self._rows(
            WorldModelRuleException, owner_id, novel_id, version_id
        )
        links = await self._rows(WorldModelEntityLink, owner_id, novel_id, version_id)
        alias_reviews = await self._rows(
            WorldModelAliasReview, owner_id, novel_id, version_id
        )

        stored_hash = entities[0].projection_hash
        if any(
            row.projection_hash != stored_hash
            for row in (
                *entities,
                *rules,
                *exceptions,
                *links,
                *alias_reviews,
            )
        ):
            raise WorldEntityRepositoryError(
                "projection rows disagree on the sealed projection hash"
            )

        entity_rows = [WorldEntity.model_validate(row.canonical_payload) for row in entities]
        rule_rows = [WorldRule.model_validate(row.canonical_payload) for row in rules]
        exception_rows = [
            RuleException.model_validate(row.canonical_payload) for row in exceptions
        ]
        link_rows = [EntityLink.model_validate(row.canonical_payload) for row in links]
        review_rows = [
            AliasCollisionReview.model_validate(row.canonical_payload)
            for row in alias_reviews
        ]

        for row, entity in zip(entities, entity_rows):
            self._check_checksum(row, entity_checksum(entity), "entity")
            self._check_chat_free(row)
        for row, rule in zip(rules, rule_rows):
            self._check_checksum(row, rule_checksum(rule), "rule")
            self._check_chat_free(row)
        for row, exception in zip(exceptions, exception_rows):
            self._check_checksum(row, exception_checksum(exception), "exception")
            self._check_chat_free(row)
        for row, link in zip(links, link_rows):
            self._check_checksum(row, link_checksum(link), "link")
            self._check_chat_free(row)
        for row, review in zip(alias_reviews, review_rows):
            self._check_checksum(row, alias_review_checksum(review), "alias review")
            self._check_chat_free(row)

        rebuilt = EntityCandidateProjection(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            schema_version=entities[0].schema_version or ENTITY_SCHEMA_VERSION,
            entities=tuple(entity_rows),
            links=tuple(link_rows),
            rules=tuple(rule_rows),
            exceptions=tuple(exception_rows),
            alias_reviews=tuple(review_rows),
            projection_hash=stored_hash,
        )
        if entity_projection_checksum(rebuilt) != stored_hash:
            raise WorldEntityRepositoryError("projection checksum drift on replay")
        return rebuilt

    async def list_versions(self, *, owner_id: int, novel_id: int) -> list[int]:
        """Ascending version lineage for one owner/novel scope."""
        rows = (
            await self._session.scalars(
                select(WorldModelEntity.version_id)
                .where(
                    WorldModelEntity.owner_id == owner_id,
                    WorldModelEntity.novel_id == novel_id,
                )
                .distinct()
                .order_by(WorldModelEntity.version_id.asc())
            )
        ).all()
        return list(rows)

    # ---------------------------------------------------------------- helpers

    async def _rows(self, model, owner_id: int, novel_id: int, version_id: int):
        return (
            await self._session.scalars(
                select(model)
                .where(
                    model.owner_id == owner_id,
                    model.novel_id == novel_id,
                    model.version_id == version_id,
                )
                .order_by(model.id.asc())
            )
        ).all()

    async def _assert_version_not_stale(
        self, projection: EntityCandidateProjection
    ) -> None:
        max_version = await self._session.scalar(
            select(func.max(WorldModelEntity.version_id)).where(
                WorldModelEntity.owner_id == projection.owner_id,
                WorldModelEntity.novel_id == projection.novel_id,
            )
        )
        if max_version is not None and projection.version_id < int(max_version):
            raise WorldEntityRepositoryError(
                f"stale-version write rejected: newest version "
                f"is {max_version}, tried {projection.version_id}"
            )

    def _check_checksum(self, row, checksum: str, label: str) -> None:
        if row.canonical_payload_hash != checksum:
            raise WorldEntityRepositoryError(f"{label} checksum drift on replay")

    def _check_chat_free(self, row) -> None:
        source_kind = getattr(row, "source_kind", None)
        if source_kind in {kind.value for kind in CHAT_SOURCE_KINDS}:
            raise WorldEntityRepositoryError(
                "replayed row carries a Reader Chat source kind (D-06)"
            )

    def _to_rows(self, projection: EntityCandidateProjection) -> list[object]:
        rows: list[object] = []
        for entity in projection.entities:
            rows.append(
                WorldModelEntity(
                    entity_key=entity.entity_key,
                    entity_type=entity.entity_type.value,
                    disclosure_cutoff=entity.disclosure_cutoff,
                    source_kind=entity.source_kind.value,
                    authority=entity.authority.value,
                    confidence=entity.confidence,
                    gate_status=entity.gate_status.value,
                    source_refs=[
                        ref.model_dump(mode="json") for ref in entity.source_refs
                    ],
                    aliases=[alias.model_dump(mode="json") for alias in entity.aliases],
                    lineage=list(entity.lineage),
                    owner_id=entity.owner_id,
                    novel_id=entity.novel_id,
                    version_id=entity.version_id,
                    canonical_payload=entity.model_dump(mode="json"),
                    canonical_payload_hash=entity.checksum,
                    idempotency_key=entity.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        for rule in projection.rules:
            rows.append(
                WorldModelRule(
                    rule_key=rule.rule_key,
                    disclosure_cutoff=rule.disclosure_cutoff,
                    source_kind=rule.source_kind.value,
                    authority=rule.authority.value,
                    confidence=rule.confidence,
                    gate_status=rule.gate_status.value,
                    source_refs=[
                        ref.model_dump(mode="json") for ref in rule.source_refs
                    ],
                    lineage=list(rule.lineage),
                    owner_id=rule.owner_id,
                    novel_id=rule.novel_id,
                    version_id=rule.version_id,
                    canonical_payload=rule.model_dump(mode="json"),
                    canonical_payload_hash=rule.checksum,
                    idempotency_key=rule.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        for exception in projection.exceptions:
            rows.append(
                WorldModelRuleException(
                    exception_key=exception.exception_key,
                    rule_key=exception.rule_key,
                    applies_to=exception.applies_to,
                    disclosure_cutoff=exception.disclosure_cutoff,
                    source_kind=exception.source_kind.value,
                    authority=exception.authority.value,
                    confidence=exception.confidence,
                    gate_status=exception.gate_status.value,
                    source_refs=[
                        ref.model_dump(mode="json") for ref in exception.source_refs
                    ],
                    owner_id=exception.owner_id,
                    novel_id=exception.novel_id,
                    version_id=exception.version_id,
                    canonical_payload=exception.model_dump(mode="json"),
                    canonical_payload_hash=exception.checksum,
                    idempotency_key=exception.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        for link in projection.links:
            rows.append(
                WorldModelEntityLink(
                    link_key=link.link_key,
                    link_kind=link.link_kind.value,
                    source_key=link.source_key,
                    target_key=link.target_key,
                    disclosure_cutoff=link.disclosure_cutoff,
                    source_kind=link.source_kind.value,
                    authority=link.authority.value,
                    confidence=link.confidence,
                    gate_status=link.gate_status.value,
                    source_refs=[
                        ref.model_dump(mode="json") for ref in link.source_refs
                    ],
                    owner_id=link.owner_id,
                    novel_id=link.novel_id,
                    version_id=link.version_id,
                    canonical_payload=link.model_dump(mode="json"),
                    canonical_payload_hash=link.checksum,
                    idempotency_key=link.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        for review in projection.alias_reviews:
            rows.append(
                WorldModelAliasReview(
                    review_key=review.review_key,
                    entity_key_a=review.entity_key_a,
                    entity_key_b=review.entity_key_b,
                    matched_alias=review.matched_alias,
                    similarity=review.similarity,
                    review_status=review.status.value,
                    disclosure_cutoff=review.disclosure_cutoff,
                    source_kind="canon_source",
                    source_refs=[
                        ref.model_dump(mode="json") for ref in review.source_refs
                    ],
                    owner_id=review.owner_id,
                    novel_id=review.novel_id,
                    version_id=review.version_id,
                    canonical_payload=review.model_dump(mode="json"),
                    canonical_payload_hash=review.checksum,
                    idempotency_key=review.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        return rows

    async def _replay_existing_or_fail(
        self, projection: EntityCandidateProjection
    ) -> None:
        """After a unique-key race, replay the winner instead of duplicating."""
        replayed = await self.replay_projection(
            owner_id=projection.owner_id,
            novel_id=projection.novel_id,
            version_id=projection.version_id,
        )
        if replayed.idempotency_key != projection.idempotency_key:
            raise WorldEntityRepositoryError(
                "idempotent replay race: winner differs from this projection"
            )
