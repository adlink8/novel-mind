"""Read-only optional timeline/relationship/clue adapters for builder packages."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import NarrativeMemoryVersion
from app.services.narrative_memory.builder_contracts import (
    OptionalSourceSignal,
    SourceStatus,
)


async def load_optional_signals(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version: NarrativeMemoryVersion,
    chapter_number: int | None = None,
) -> list[OptionalSourceSignal]:
    """Return typed optional signals with explicit source status.

    Never fabricates healthy empty for outages. Never reads Reader Chat.
    """

    lineage = version.optional_source_lineage or {}
    # optional_source_lineage is stored as list from authority; normalize.
    by_kind: dict[str, dict[str, Any]] = {}
    if isinstance(lineage, list):
        for item in lineage:
            if isinstance(item, dict) and "kind" in item:
                by_kind[str(item["kind"])] = item
    elif isinstance(lineage, dict):
        by_kind = {str(k): v for k, v in lineage.items() if isinstance(v, dict)}

    return [
        await _timeline_signal(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version=version,
            expected=by_kind.get("timeline"),
            chapter_number=chapter_number,
        ),
        await _relationship_signal(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version=version,
            expected=by_kind.get("relationship"),
            chapter_number=chapter_number,
        ),
        await _clue_signal(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version=version,
            expected=by_kind.get("clue"),
            chapter_number=chapter_number,
        ),
    ]


async def _timeline_signal(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version: NarrativeMemoryVersion,
    expected: dict[str, Any] | None,
    chapter_number: int | None,
) -> OptionalSourceSignal:
    if expected and expected.get("status") in {
        "optional_unavailable",
        "blocked",
        "rebuild_required",
    }:
        return OptionalSourceSignal(
            source_kind="timeline",
            status=SourceStatus.UNAVAILABLE
            if expected.get("status") == "optional_unavailable"
            else SourceStatus.LINEAGE_MISMATCH,
            reason_code=str(expected.get("status")),
            lineage={"version_id": expected.get("version_id")},
        )
    try:
        row = await session.execute(
            text(
                """
                SELECT id, source_snapshot_hash, hierarchy_build_id, hierarchy_checksum
                FROM analysis_versions
                WHERE owner_id = :owner AND novel_id = :novel
                  AND status = 'validated'
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {"owner": owner_id, "novel": novel_id},
        )
        version_row = row.mappings().first()
    except Exception:  # noqa: BLE001 - table may be absent in partial fixtures
        return OptionalSourceSignal(
            source_kind="timeline",
            status=SourceStatus.UNAVAILABLE,
            reason_code="timeline_query_failed",
        )
    if version_row is None:
        return OptionalSourceSignal(
            source_kind="timeline",
            status=SourceStatus.HEALTHY_EMPTY,
            reason_code="no_validated_timeline",
        )
    if (
        version_row["source_snapshot_hash"] != version.source_snapshot_hash
        or version_row["hierarchy_build_id"] != version.hierarchy_build_id
    ):
        return OptionalSourceSignal(
            source_kind="timeline",
            status=SourceStatus.LINEAGE_MISMATCH,
            reason_code="timeline_lineage_mismatch",
            lineage={"version_id": version_row["id"]},
        )
    count_row = await session.execute(
        text(
            """
            SELECT count(*) AS n
            FROM machine_timeline_events
            WHERE version_id = :version
              AND (:chapter IS NULL OR chapter_number = :chapter)
            """
        ),
        {"version": version_row["id"], "chapter": chapter_number},
    )
    count = int(count_row.scalar_one())
    return OptionalSourceSignal(
        source_kind="timeline",
        status=SourceStatus.NON_EMPTY if count else SourceStatus.HEALTHY_EMPTY,
        reason_code=None if count else "timeline_empty",
        signal_keys=tuple(f"timeline:event:{i}" for i in range(min(count, 5))),
        lineage={
            "version_id": version_row["id"],
            "hierarchy_build_id": version_row["hierarchy_build_id"],
        },
    )


async def _relationship_signal(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version: NarrativeMemoryVersion,
    expected: dict[str, Any] | None,
    chapter_number: int | None,
) -> OptionalSourceSignal:
    if expected and expected.get("status") == "optional_unavailable":
        return OptionalSourceSignal(
            source_kind="relationship",
            status=SourceStatus.UNAVAILABLE,
            reason_code="relationship_unavailable",
        )
    try:
        count_row = await session.execute(
            text(
                """
                SELECT count(*) AS n
                FROM relationship_observations
                WHERE owner_id = :owner AND novel_id = :novel
                  AND status = 'accepted'
                  AND (:chapter IS NULL OR observed_chapter <= :chapter)
                """
            ),
            {"owner": owner_id, "novel": novel_id, "chapter": chapter_number},
        )
        count = int(count_row.scalar_one())
    except Exception:  # noqa: BLE001
        return OptionalSourceSignal(
            source_kind="relationship",
            status=SourceStatus.UNAVAILABLE,
            reason_code="relationship_query_failed",
        )
    return OptionalSourceSignal(
        source_kind="relationship",
        status=SourceStatus.NON_EMPTY if count else SourceStatus.HEALTHY_EMPTY,
        signal_keys=tuple(f"relationship:obs:{i}" for i in range(min(count, 5))),
        lineage={"owner_id": owner_id, "novel_id": novel_id},
    )


async def _clue_signal(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version: NarrativeMemoryVersion,
    expected: dict[str, Any] | None,
    chapter_number: int | None,
) -> OptionalSourceSignal:
    if expected and expected.get("status") == "optional_unavailable":
        return OptionalSourceSignal(
            source_kind="clue",
            status=SourceStatus.UNAVAILABLE,
            reason_code="clue_unavailable",
        )
    try:
        pointer = await session.execute(
            text(
                """
                SELECT p.version_id, v.status, v.source_snapshot_hash, v.hierarchy_build_id
                FROM clue_active_pointers p
                JOIN clue_analysis_versions v ON v.id = p.version_id
                WHERE p.owner_id = :owner AND p.novel_id = :novel
                """
            ),
            {"owner": owner_id, "novel": novel_id},
        )
        row = pointer.mappings().first()
    except Exception:  # noqa: BLE001
        return OptionalSourceSignal(
            source_kind="clue",
            status=SourceStatus.UNAVAILABLE,
            reason_code="clue_query_failed",
        )
    if row is None:
        return OptionalSourceSignal(
            source_kind="clue",
            status=SourceStatus.HEALTHY_EMPTY,
            reason_code="no_clue_pointer",
        )
    if row["status"] != "validated":
        return OptionalSourceSignal(
            source_kind="clue",
            status=SourceStatus.UNAVAILABLE,
            reason_code=f"clue_status_{row['status']}",
            lineage={"version_id": row["version_id"]},
        )
    if (
        row["source_snapshot_hash"] != version.source_snapshot_hash
        or row["hierarchy_build_id"] != version.hierarchy_build_id
    ):
        return OptionalSourceSignal(
            source_kind="clue",
            status=SourceStatus.LINEAGE_MISMATCH,
            reason_code="clue_lineage_mismatch",
            lineage={"version_id": row["version_id"]},
        )
    count_row = await session.execute(
        text("SELECT count(*) FROM machine_clues WHERE version_id = :version"),
        {"version": row["version_id"]},
    )
    count = int(count_row.scalar_one())
    return OptionalSourceSignal(
        source_kind="clue",
        status=SourceStatus.NON_EMPTY if count else SourceStatus.HEALTHY_EMPTY,
        signal_keys=tuple(f"clue:{i}" for i in range(min(count, 5))),
        lineage={"version_id": row["version_id"]},
    )
