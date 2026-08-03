"""Versioned world-entity query API (REQ-WM-03, D-02/D-03/D-05).

The read-only layer mirrors ``entities.WorldEntityQueryEngine`` against the five
durable tables. Query order is strictly scoped first, then filtered (D-05):

1. Scope: owner / novel / version (SQL-scoped).
2. Disclosure: rows with ``disclosure_cutoff <= cutoff`` — hidden entities,
   rules, exceptions, links and alias reviews never leak early.
3. Cross-reference: links are only returned when both endpoints are visible;
   exceptions when their rule (and target entity) are visible.

Rule exceptions and alias collision reviews remain first-class query results:
exceptions stay bound to their rules and alias collisions stay reviewable after
any number of restarts. This module never writes, never merges, and never
promotes.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.world_model_entity import (
    WorldModelAliasReview,
    WorldModelEntity,
    WorldModelEntityLink,
    WorldModelRule,
    WorldModelRuleException,
)
from app.services.world_model.entities import (
    AliasCollisionReview,
    AliasReviewStatus,
    EntityCandidateProjection,
    EntityLink,
    EntityType,
    LinkKind,
    WorldEntity,
    WorldEntityQueryEngine,
    build_entity_projection,
)
from app.services.world_model.rules import RuleException, WorldRule


class WorldEntityQueryError(ValueError):
    pass


class WorldEntityQueries:
    """Read-only, owner-scoped, version/cutoff-aware query API."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------- queries

    async def query_world_projection(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        cutoff: int,
    ) -> EntityCandidateProjection | None:
        """Reader view of one version at ``cutoff`` (D-05).

        Returns ``None`` when no visible entity exists in the owner scope (fail
        closed). Rows with a later disclosure are never returned and no raw row
        leaks across the cutoff.
        """
        entities = (
            await self._session.scalars(
                select(WorldModelEntity)
                .where(
                    WorldModelEntity.owner_id == owner_id,
                    WorldModelEntity.novel_id == novel_id,
                    WorldModelEntity.version_id == version_id,
                    WorldModelEntity.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelEntity.id.asc())
            )
        ).all()
        if not entities:
            return None
        visible_entity_keys = {row.entity_key for row in entities}

        link_rows = (
            await self._session.scalars(
                select(WorldModelEntityLink)
                .where(
                    WorldModelEntityLink.owner_id == owner_id,
                    WorldModelEntityLink.novel_id == novel_id,
                    WorldModelEntityLink.version_id == version_id,
                    WorldModelEntityLink.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelEntityLink.id.asc())
            )
        ).all()
        links: list[EntityLink] = []
        for row in link_rows:
            link = EntityLink.model_validate(row.canonical_payload)
            if (
                link.source_key in visible_entity_keys
                and link.target_key in visible_entity_keys
            ):
                links.append(link)

        rule_rows = (
            await self._session.scalars(
                select(WorldModelRule)
                .where(
                    WorldModelRule.owner_id == owner_id,
                    WorldModelRule.novel_id == novel_id,
                    WorldModelRule.version_id == version_id,
                    WorldModelRule.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelRule.id.asc())
            )
        ).all()
        rules = [WorldRule.model_validate(row.canonical_payload) for row in rule_rows]
        visible_rule_keys = {rule.rule_key for rule in rules}

        exception_rows = (
            await self._session.scalars(
                select(WorldModelRuleException)
                .where(
                    WorldModelRuleException.owner_id == owner_id,
                    WorldModelRuleException.novel_id == novel_id,
                    WorldModelRuleException.version_id == version_id,
                    WorldModelRuleException.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelRuleException.id.asc())
            )
        ).all()
        exceptions: list[RuleException] = []
        for row in exception_rows:
            exception = RuleException.model_validate(row.canonical_payload)
            if exception.rule_key not in visible_rule_keys:
                continue
            if (
                exception.applies_to is not None
                and exception.applies_to not in visible_entity_keys
            ):
                continue
            exceptions.append(exception)

        review_rows = (
            await self._session.scalars(
                select(WorldModelAliasReview)
                .where(
                    WorldModelAliasReview.owner_id == owner_id,
                    WorldModelAliasReview.novel_id == novel_id,
                    WorldModelAliasReview.version_id == version_id,
                    WorldModelAliasReview.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelAliasReview.id.asc())
            )
        ).all()
        alias_reviews: list[AliasCollisionReview] = []
        for row in review_rows:
            review = AliasCollisionReview.model_validate(row.canonical_payload)
            if (
                review.entity_key_a in visible_entity_keys
                and review.entity_key_b in visible_entity_keys
            ):
                alias_reviews.append(review)

        return build_entity_projection(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            entities=[WorldEntity.model_validate(row.canonical_payload) for row in entities],
            links=links,
            rules=rules,
            exceptions=exceptions,
            alias_reviews=alias_reviews,
        )

    async def query_entities(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        entity_type: EntityType | None = None,
    ) -> tuple[WorldEntity, ...]:
        """All typed entity/faction/place/item rows for one version (author view)."""
        stmt = select(WorldModelEntity).where(
            WorldModelEntity.owner_id == owner_id,
            WorldModelEntity.novel_id == novel_id,
            WorldModelEntity.version_id == version_id,
        )
        if entity_type is not None:
            stmt = stmt.where(WorldModelEntity.entity_type == entity_type.value)
        rows = (
            await self._session.scalars(
                stmt.order_by(WorldModelEntity.entity_key.asc())
            )
        ).all()
        return tuple(WorldEntity.model_validate(row.canonical_payload) for row in rows)

    async def query_links(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        link_kind: LinkKind | None = None,
    ) -> tuple[EntityLink, ...]:
        """Membership/ownership/spatial/item-state links for one version."""
        stmt = select(WorldModelEntityLink).where(
            WorldModelEntityLink.owner_id == owner_id,
            WorldModelEntityLink.novel_id == novel_id,
            WorldModelEntityLink.version_id == version_id,
        )
        if link_kind is not None:
            stmt = stmt.where(WorldModelEntityLink.link_kind == link_kind.value)
        rows = (
            await self._session.scalars(
                stmt.order_by(WorldModelEntityLink.link_key.asc())
            )
        ).all()
        return tuple(EntityLink.model_validate(row.canonical_payload) for row in rows)

    async def query_rules(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
    ) -> tuple[WorldRule, ...]:
        """All world rules for one version (author view)."""
        rows = (
            await self._session.scalars(
                select(WorldModelRule)
                .where(
                    WorldModelRule.owner_id == owner_id,
                    WorldModelRule.novel_id == novel_id,
                    WorldModelRule.version_id == version_id,
                )
                .order_by(WorldModelRule.rule_key.asc())
            )
        ).all()
        return tuple(WorldRule.model_validate(row.canonical_payload) for row in rows)

    async def query_rule_exceptions(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        rule_key: str | None = None,
    ) -> tuple[RuleException, ...]:
        """First-class rule exceptions; always bound to a projection-local rule."""
        stmt = select(WorldModelRuleException).where(
            WorldModelRuleException.owner_id == owner_id,
            WorldModelRuleException.novel_id == novel_id,
            WorldModelRuleException.version_id == version_id,
        )
        if rule_key is not None:
            stmt = stmt.where(WorldModelRuleException.rule_key == rule_key)
        rows = (
            await self._session.scalars(
                stmt.order_by(WorldModelRuleException.exception_key.asc())
            )
        ).all()
        return tuple(
            RuleException.model_validate(row.canonical_payload) for row in rows
        )

    async def query_alias_reviews(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        status: AliasReviewStatus = AliasReviewStatus.REVIEW,
    ) -> tuple[AliasCollisionReview, ...]:
        """Reviewable alias collisions for one version (never auto-merged)."""
        rows = (
            await self._session.scalars(
                select(WorldModelAliasReview)
                .where(
                    WorldModelAliasReview.owner_id == owner_id,
                    WorldModelAliasReview.novel_id == novel_id,
                    WorldModelAliasReview.version_id == version_id,
                    WorldModelAliasReview.review_status == status.value,
                )
                .order_by(WorldModelAliasReview.review_key.asc())
            )
        ).all()
        return tuple(
            AliasCollisionReview.model_validate(row.canonical_payload) for row in rows
        )

    async def query_entity_lineage(
        self, *, owner_id: int, novel_id: int, entity_key: str
    ) -> tuple[WorldEntity, ...]:
        """Full version lineage of one logical entity (D-03), oldest first."""
        rows = (
            await self._session.scalars(
                select(WorldModelEntity)
                .where(
                    WorldModelEntity.owner_id == owner_id,
                    WorldModelEntity.novel_id == novel_id,
                )
                .order_by(
                    WorldModelEntity.version_id.asc(),
                    WorldModelEntity.id.asc(),
                )
            )
        ).all()
        matched = [
            row
            for row in rows
            if row.entity_key == entity_key or entity_key in (row.lineage or [])
        ]
        return tuple(
            WorldEntity.model_validate(row.canonical_payload) for row in matched
        )

    async def query_rule_lineage(
        self, *, owner_id: int, novel_id: int, rule_key: str
    ) -> tuple[WorldRule, ...]:
        """Full version lineage of one logical rule, oldest first."""
        rows = (
            await self._session.scalars(
                select(WorldModelRule)
                .where(
                    WorldModelRule.owner_id == owner_id,
                    WorldModelRule.novel_id == novel_id,
                )
                .order_by(
                    WorldModelRule.version_id.asc(),
                    WorldModelRule.id.asc(),
                )
            )
        ).all()
        matched = [
            row
            for row in rows
            if row.rule_key == rule_key or rule_key in (row.lineage or [])
        ]
        return tuple(WorldRule.model_validate(row.canonical_payload) for row in matched)

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


# Re-export the pure engine for convenience (mirrors the durable query API).
WorldEntityView = WorldEntityQueryEngine
