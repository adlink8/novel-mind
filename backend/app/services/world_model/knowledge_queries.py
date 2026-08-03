"""Durable cutoff/POV epistemic query API (REQ-WM-02, D-05/D-06).

The read-only layer mirrors ``queries.py`` against the durable
``world_model_knowledge`` table. Query order is strictly scoped first, then
filtered (D-05):

1. Scope: owner / novel / version / subject / cutoff / POV (SQL-scoped).
2. Disclosure: ``known_at <= cutoff AND disclosure_cutoff <= cutoff`` — hidden
   knowledge never leaks early.
3. Authority / candidate filter: optional authority allowlist; candidate-only
   claims are reported as ``candidate_only``.

Abstention is a first-class result: a character with no knowledge at a
cutoff/POV returns ``ABSTAINED`` with nothing fabricated (D-06). This module
never writes, never promotes, and never resolves a contradiction by overwrite.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.world_model_knowledge import WorldModelKnowledge
from app.services.world_model.contracts import Authority
from app.services.world_model.knowledge import (
    EpistemicAspect,
    EpistemicClaim,
    EpistemicStatus,
)
from app.services.world_model.queries import EpistemicAnswer, EpistemicQueryEngine


class KnowledgeQueryError(ValueError):
    pass


def _claim_from_row(row: WorldModelKnowledge) -> EpistemicClaim:
    return EpistemicClaim.model_validate(row.canonical_payload)


class KnowledgeQueries:
    """Read-only, owner-scoped, cutoff/POV-aware durable query API."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query_character_knowledge(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        subject: str,
        cutoff: int,
        pov: str | None = None,
        authorities: frozenset[Authority] | None = None,
        aspect: EpistemicAspect | None = None,
    ) -> EpistemicAnswer:
        """What does ``subject`` know at ``cutoff``, scoped and disclosure-filtered."""

        stmt = select(WorldModelKnowledge).where(
            WorldModelKnowledge.owner_id == owner_id,
            WorldModelKnowledge.novel_id == novel_id,
            WorldModelKnowledge.version_id == version_id,
            WorldModelKnowledge.subject == subject,
            WorldModelKnowledge.known_at <= cutoff,
            WorldModelKnowledge.disclosure_cutoff <= cutoff,
        )
        if pov is not None:
            stmt = stmt.where(
                (WorldModelKnowledge.pov == pov)
                | (WorldModelKnowledge.pov_kind == "omniscient")
            )
        if aspect is not None:
            stmt = stmt.where(WorldModelKnowledge.aspect == aspect.value)
        if authorities is not None:
            stmt = stmt.where(
                WorldModelKnowledge.authority.in_([a.value for a in authorities])
            )
        rows = (
            await self._session.scalars(
                stmt.order_by(
                    WorldModelKnowledge.known_at.asc(),
                    WorldModelKnowledge.knowledge_key.asc(),
                )
            )
        ).all()
        return EpistemicQueryEngine(
            _claim_from_row(row) for row in rows
        ).query_character_knowledge(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            subject=subject,
            cutoff=cutoff,
            pov=None,  # already applied in SQL
            authorities=authorities,
            aspect=aspect,
        )

    async def query_character_history(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        subject: str,
        aspect: EpistemicAspect | None = None,
    ) -> tuple[EpistemicClaim, ...]:
        """Full state/goal/motivation/knowledge history for one subject.

        Authoritative author view: no cutoff/POV filter, so mistaken beliefs,
        hidden knowledge and contradictions remain queryable.
        """
        stmt = select(WorldModelKnowledge).where(
            WorldModelKnowledge.owner_id == owner_id,
            WorldModelKnowledge.novel_id == novel_id,
            WorldModelKnowledge.version_id == version_id,
            WorldModelKnowledge.subject == subject,
        )
        if aspect is not None:
            stmt = stmt.where(WorldModelKnowledge.aspect == aspect.value)
        rows = (
            await self._session.scalars(
                stmt.order_by(
                    WorldModelKnowledge.known_at.asc(),
                    WorldModelKnowledge.knowledge_key.asc(),
                )
            )
        ).all()
        return tuple(_claim_from_row(row) for row in rows)

    async def query_lineage(
        self, *, owner_id: int, novel_id: int, knowledge_key: str
    ) -> tuple[EpistemicClaim, ...]:
        """Full version lineage of one logical knowledge chain, oldest first."""
        rows = (
            await self._session.scalars(
                select(WorldModelKnowledge)
                .where(
                    WorldModelKnowledge.owner_id == owner_id,
                    WorldModelKnowledge.novel_id == novel_id,
                )
                .order_by(
                    WorldModelKnowledge.version_id.asc(),
                    WorldModelKnowledge.known_at.asc(),
                    WorldModelKnowledge.id.asc(),
                )
            )
        ).all()
        matched = [
            row
            for row in rows
            if row.knowledge_key == knowledge_key or knowledge_key in (row.lineage or [])
        ]
        return tuple(_claim_from_row(row) for row in matched)

    async def query_by_status(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        status: EpistemicStatus,
    ) -> tuple[EpistemicClaim, ...]:
        """All claims with one epistemic label (mistaken/hidden/contradiction)."""
        rows = (
            await self._session.scalars(
                select(WorldModelKnowledge)
                .where(
                    WorldModelKnowledge.owner_id == owner_id,
                    WorldModelKnowledge.novel_id == novel_id,
                    WorldModelKnowledge.version_id == version_id,
                    WorldModelKnowledge.epistemic_status == status.value,
                )
                .order_by(
                    WorldModelKnowledge.known_at.asc(),
                    WorldModelKnowledge.knowledge_key.asc(),
                )
            )
        ).all()
        return tuple(_claim_from_row(row) for row in rows)

    async def list_versions(self, *, owner_id: int, novel_id: int) -> list[int]:
        """Ascending version lineage for one owner/novel scope."""
        rows = (
            await self._session.scalars(
                select(WorldModelKnowledge.version_id)
                .where(
                    WorldModelKnowledge.owner_id == owner_id,
                    WorldModelKnowledge.novel_id == novel_id,
                )
                .distinct()
                .order_by(WorldModelKnowledge.version_id.asc())
            )
        ).all()
        return list(rows)
