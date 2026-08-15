"""Identity / override / evidence loading mixin for the relationship query facade.

Extracted from ``query.py`` (refactor split): this mixin owns the append-only
character merge map (``_identity_map``), the latest-wins relationship override
field patches (``_active_relationship_overrides``), and the deterministic
evidence-link rows per observation (``_evidence_for_observations``). It never
imports ``query.py`` — only models/schemas/stdlib — keeping the query package
dependency graph acyclic. The composed class ``RelationshipGraphQueryService``
keeps the same method surface.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.relationship import (
    CharacterIdentityOverride,
    RelationshipEvidenceLink,
    RelationshipOverride,
)


class IdentityQueryMixin:
    """Identity / override / evidence seams (see module docstring)."""

    async def _identity_map(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
    ) -> dict[int, int]:
        """Latest-wins character merge map from append-only identity overrides."""

        rows = list(
            (
                await session.scalars(
                    select(CharacterIdentityOverride)
                    .where(
                        CharacterIdentityOverride.owner_id == owner_id,
                        CharacterIdentityOverride.novel_id == novel_id,
                        CharacterIdentityOverride.analysis_version_id == version_id,
                    )
                    .order_by(CharacterIdentityOverride.id)
                )
            ).all()
        )
        # Latest row by canonical target wins; needs_relink does not apply merges.
        latest_by_merged: dict[int, CharacterIdentityOverride] = {}
        for row in rows:
            for mid in row.merged_character_ids or []:
                latest_by_merged[int(mid)] = row
        mapping: dict[int, int] = {}
        for mid, row in latest_by_merged.items():
            if row.status != "active":
                continue
            mapping[mid] = row.canonical_character_id
            mapping[row.canonical_character_id] = row.canonical_character_id
        return mapping

    async def _active_relationship_overrides(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
    ) -> dict[str, dict[str, Any]]:
        """Latest-wins field patches per logical key (append-only supersession)."""

        rows = list(
            (
                await session.scalars(
                    select(RelationshipOverride)
                    .where(
                        RelationshipOverride.owner_id == owner_id,
                        RelationshipOverride.novel_id == novel_id,
                        RelationshipOverride.analysis_version_id == version_id,
                    )
                    .order_by(RelationshipOverride.id)
                )
            ).all()
        )
        # Highest id per (logical_key, field_name) wins; only status=active applies.
        latest: dict[tuple[str, str], RelationshipOverride] = {}
        for row in rows:
            latest[(row.logical_relationship_key, row.field_name)] = row

        result: dict[str, dict[str, Any]] = defaultdict(dict)
        for (key, field), row in latest.items():
            if row.status != "active":
                continue
            result[key][field] = deepcopy(row.value)
        return dict(result)

    async def _evidence_for_observations(
        self,
        session: AsyncSession,
        *,
        observation_ids: list[int],
    ) -> dict[int, list[RelationshipEvidenceLink]]:
        if not observation_ids:
            return {}
        rows = list(
            (
                await session.scalars(
                    select(RelationshipEvidenceLink)
                    .where(RelationshipEvidenceLink.observation_id.in_(observation_ids))
                    .order_by(
                        RelationshipEvidenceLink.observation_id,
                        RelationshipEvidenceLink.sort_order,
                        RelationshipEvidenceLink.id,
                    )
                )
            ).all()
        )
        out: dict[int, list[RelationshipEvidenceLink]] = defaultdict(list)
        for row in rows:
            out[row.observation_id].append(row)
        return dict(out)


__all__ = ["IdentityQueryMixin"]
