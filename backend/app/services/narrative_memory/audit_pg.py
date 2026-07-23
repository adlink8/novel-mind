"""SELECT-only PostgreSQL inventory for narrative-memory asset audits."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.analysis import AnalysisVersion
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.clue import ClueActivePointer, ClueAnalysisVersion, MachineClue
from app.models.novel import Chapter, Novel
from app.models.relationship import RelationshipBuildRun, RelationshipObservation
from app.models.timeline import MachineTimelineEvent, TimelineActivePointer
from app.services.chunking.hierarchy import validate_hierarchy_invariants
from app.services.chunking.manifests import content_hash
from app.services.chunking.pg_store import _node_from_row
from app.services.narrative_memory.audit_contracts import (
    AssetInventory,
    AssetKind,
    ReasonCode,
    RebuildRange,
)
from app.services.rag_fixture import stable_hash


class PostgresAuditSource:
    """Observe current authorities without flush, repair, dispatch, or promotion."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def inventory(
        self, *, owner_id: int, novel_id: int
    ) -> tuple[AssetInventory, ...]:
        novel = await self._session.scalar(
            select(Novel).where(Novel.id == novel_id, Novel.owner_id == owner_id)
        )
        if novel is None:
            missing = AssetInventory(
                kind=AssetKind.HIERARCHY,
                owner_id=owner_id,
                novel_id=novel_id,
                available=False,
                reason_codes=(ReasonCode.SOURCE_MISSING,),
            )
            return (missing, *self._unavailable_optionals(owner_id, novel_id))

        hierarchy, build = await self._hierarchy_inventory(owner_id, novel)
        optionals = await self._optional_inventories(owner_id, novel_id, build)
        return (hierarchy, *optionals)

    @staticmethod
    def _unavailable_optionals(
        owner_id: int, novel_id: int
    ) -> tuple[AssetInventory, ...]:
        return tuple(
            AssetInventory(
                kind=kind,
                owner_id=owner_id,
                novel_id=novel_id,
                available=False,
                reason_codes=(ReasonCode.SOURCE_UNAVAILABLE,),
            )
            for kind in (AssetKind.TIMELINE, AssetKind.RELATIONSHIP, AssetKind.CLUE)
        )

    async def _hierarchy_inventory(
        self, owner_id: int, novel: Novel
    ) -> tuple[AssetInventory, ChunkBuild | None]:
        pointer = await self._session.scalar(
            select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel.id)
        )
        if pointer is None:
            return (
                AssetInventory(
                    kind=AssetKind.HIERARCHY,
                    owner_id=owner_id,
                    novel_id=novel.id,
                    available=False,
                    reason_codes=(ReasonCode.ACTIVE_VERSION_MISSING,),
                ),
                None,
            )

        build = await self._session.scalar(
            select(ChunkBuild).where(
                ChunkBuild.build_id == pointer.build_id,
                ChunkBuild.novel_id == novel.id,
            )
        )
        if build is None:
            return (
                AssetInventory(
                    kind=AssetKind.HIERARCHY,
                    owner_id=owner_id,
                    novel_id=novel.id,
                    available=False,
                    reason_codes=(ReasonCode.SOURCE_MISSING,),
                ),
                None,
            )

        chapters = list(
            (
                await self._session.scalars(
                    select(Chapter)
                    .options(undefer(Chapter.content))
                    .where(Chapter.novel_id == novel.id)
                    .order_by(Chapter.chapter_number, Chapter.id)
                )
            ).all()
        )
        all_build_rows = list(
            (
                await self._session.scalars(
                    select(ChunkHierarchyNode).where(
                        ChunkHierarchyNode.build_id == build.build_id,
                    )
                )
            ).all()
        )
        reasons: set[ReasonCode] = set()
        affected: set[int] = set()
        if (
            not build.immutable
            or build.is_candidate
            or build.status not in {"built", "committed"}
        ):
            reasons.add(ReasonCode.STALE_ASSET)
            affected.update(chapter.chapter_number for chapter in chapters)
        if any(row.novel_id != novel.id for row in all_build_rows):
            reasons.add(ReasonCode.NOVEL_SCOPE_MISMATCH)
            affected.update(chapter.chapter_number for chapter in chapters)
        rows = [row for row in all_build_rows if row.novel_id == novel.id]

        expected_snapshot = stable_hash(
            {
                "novel_id": novel.id,
                "chapters": [
                    {"id": chapter.id, "h": stable_hash({"c": chapter.content or ""})}
                    for chapter in chapters
                ],
            }
        )
        if build.source_snapshot_hash != expected_snapshot:
            reasons.add(ReasonCode.SOURCE_SNAPSHOT_MISMATCH)
            affected.update(chapter.chapter_number for chapter in chapters)

        rows_by_chapter: dict[int, list[ChunkHierarchyNode]] = defaultdict(list)
        for row in rows:
            rows_by_chapter[row.chapter_id].append(row)
        chapter_by_id = {chapter.id: chapter for chapter in chapters}
        if set(rows_by_chapter) != set(chapter_by_id):
            reasons.add(ReasonCode.INCOMPLETE_COVERAGE)
            affected.update(
                chapter.chapter_number
                for chapter in chapters
                if chapter.id not in rows_by_chapter
            )

        tree_checksums: list[str] = []
        for chapter in chapters:
            chapter_rows = rows_by_chapter.get(chapter.id, [])
            if not chapter_rows:
                continue
            nodes = [_node_from_row(row) for row in chapter_rows]
            try:
                validate_hierarchy_invariants(nodes, chapter_id=chapter.id)
            except (KeyError, ValueError):
                reasons.add(ReasonCode.MALFORMED_HIERARCHY)
                affected.add(chapter.chapter_number)

            for node in nodes:
                if node.content_hash != content_hash(node.content):
                    reasons.add(ReasonCode.CONTENT_HASH_MISMATCH)
                    affected.add(chapter.chapter_number)
                if (
                    node.source_start < 0
                    or node.source_end < node.source_start
                    or node.source_end > len(chapter.content or "")
                ):
                    reasons.add(ReasonCode.INVALID_OFFSET)
                    affected.add(chapter.chapter_number)
                if (
                    node.level == "evidence"
                    and (chapter.content or "")[node.source_start : node.source_end]
                    != node.content
                ):
                    reasons.add(ReasonCode.CONTENT_HASH_MISMATCH)
                    affected.add(chapter.chapter_number)

            ordered_nodes = sorted(
                nodes,
                key=lambda node: (
                    {"chapter": 0, "scene": 1, "evidence": 2}.get(node.level, 9),
                    node.order_index,
                    node.node_id,
                ),
            )
            tree_checksums.append(
                stable_hash(
                    {
                        "novel_id": novel.id,
                        "chapter_id": chapter.id,
                        "node_ids": [node.node_id for node in ordered_nodes],
                        "parents": {
                            node.node_id: node.parent_id for node in ordered_nodes
                        },
                    }
                )
            )

        expected_manifest = stable_hash(
            {
                "build_id": build.build_id,
                "trees": tree_checksums,
                "snapshot": build.source_snapshot_hash,
                "cfg": build.chunker_config_hash,
            }
        )
        if build.manifest_checksum != expected_manifest:
            reasons.add(ReasonCode.MANIFEST_MISMATCH)
            affected.update(chapter.chapter_number for chapter in chapters)

        return (
            AssetInventory(
                kind=AssetKind.HIERARCHY,
                owner_id=owner_id,
                novel_id=novel.id,
                version_id=build.build_id,
                source_snapshot_hash=build.source_snapshot_hash,
                manifest_hash=build.manifest_checksum,
                item_count=len(rows),
                reason_codes=tuple(reasons),
                rebuild_ranges=self._coalesce_ranges(affected),
            ),
            build,
        )

    async def _optional_inventories(
        self, owner_id: int, novel_id: int, build: ChunkBuild | None
    ) -> tuple[AssetInventory, ...]:
        if build is None:
            return self._unavailable_optionals(owner_id, novel_id)
        timeline = await self._timeline_inventory(owner_id, novel_id, build)
        relationship = await self._relationship_inventory(owner_id, novel_id, build)
        clue = await self._clue_inventory(owner_id, novel_id, build)
        return (timeline, relationship, clue)

    async def _timeline_inventory(
        self, owner_id: int, novel_id: int, build: ChunkBuild
    ) -> AssetInventory:
        pointer = await self._session.scalar(
            select(TimelineActivePointer).where(
                TimelineActivePointer.owner_id == owner_id,
                TimelineActivePointer.novel_id == novel_id,
            )
        )
        if pointer is None:
            return self._optional_unavailable(AssetKind.TIMELINE, owner_id, novel_id)
        version = await self._session.get(AnalysisVersion, pointer.version_id)
        reasons = self._lineage_reasons(
            version,
            build,
            owner_id=owner_id,
            novel_id=novel_id,
            pointer_manifest=pointer.manifest_checksum,
            allowed_statuses={"active"},
        )
        item_count = int(
            await self._session.scalar(
                select(func.count())
                .select_from(MachineTimelineEvent)
                .where(
                    MachineTimelineEvent.version_id == pointer.version_id,
                    MachineTimelineEvent.owner_id == owner_id,
                    MachineTimelineEvent.novel_id == novel_id,
                )
            )
            or 0
        )
        return self._optional_result(
            AssetKind.TIMELINE,
            owner_id,
            novel_id,
            str(pointer.version_id),
            reasons,
            item_count=item_count,
        )

    async def _relationship_inventory(
        self, owner_id: int, novel_id: int, build: ChunkBuild
    ) -> AssetInventory:
        run = await self._session.scalar(
            select(RelationshipBuildRun)
            .where(
                RelationshipBuildRun.owner_id == owner_id,
                RelationshipBuildRun.novel_id == novel_id,
                RelationshipBuildRun.status == "completed",
            )
            .order_by(RelationshipBuildRun.id.desc())
            .limit(1)
        )
        if run is None:
            return self._optional_unavailable(
                AssetKind.RELATIONSHIP, owner_id, novel_id
            )
        version = await self._session.get(AnalysisVersion, run.analysis_version_id)
        reasons = self._lineage_reasons(
            version,
            build,
            owner_id=owner_id,
            novel_id=novel_id,
            pointer_manifest=None,
            allowed_statuses={"active", "superseded"},
        )
        actual_count = int(
            await self._session.scalar(
                select(func.count())
                .select_from(RelationshipObservation)
                .where(
                    RelationshipObservation.analysis_version_id
                    == run.analysis_version_id,
                    RelationshipObservation.owner_id == owner_id,
                    RelationshipObservation.novel_id == novel_id,
                )
            )
            or 0
        )
        if actual_count != run.accepted_count:
            reasons = (ReasonCode.OPTIONAL_LINEAGE_MISMATCH,)
        return self._optional_result(
            AssetKind.RELATIONSHIP,
            owner_id,
            novel_id,
            str(run.analysis_version_id),
            reasons,
            item_count=actual_count,
        )

    async def _clue_inventory(
        self, owner_id: int, novel_id: int, build: ChunkBuild
    ) -> AssetInventory:
        pointer = await self._session.scalar(
            select(ClueActivePointer).where(
                ClueActivePointer.owner_id == owner_id,
                ClueActivePointer.novel_id == novel_id,
            )
        )
        if pointer is None:
            return self._optional_unavailable(AssetKind.CLUE, owner_id, novel_id)
        version = await self._session.get(ClueAnalysisVersion, pointer.version_id)
        reasons: tuple[ReasonCode, ...] = ()
        if (
            version is None
            or version.owner_id != owner_id
            or version.novel_id != novel_id
            or version.status != "validated"
            or version.source_snapshot_hash != build.source_snapshot_hash
            or version.hierarchy_build_id != build.build_id
            or version.hierarchy_checksum != build.manifest_checksum
            or version.manifest_checksum != pointer.manifest_checksum
        ):
            reasons = (ReasonCode.OPTIONAL_LINEAGE_MISMATCH,)
        item_count = int(
            await self._session.scalar(
                select(func.count())
                .select_from(MachineClue)
                .where(
                    MachineClue.version_id == pointer.version_id,
                    MachineClue.owner_id == owner_id,
                    MachineClue.novel_id == novel_id,
                )
            )
            or 0
        )
        return self._optional_result(
            AssetKind.CLUE,
            owner_id,
            novel_id,
            str(pointer.version_id),
            reasons,
            item_count=item_count,
        )

    @staticmethod
    def _lineage_reasons(
        version: AnalysisVersion | None,
        build: ChunkBuild,
        *,
        owner_id: int,
        novel_id: int,
        pointer_manifest: str | None,
        allowed_statuses: set[str],
    ) -> tuple[ReasonCode, ...]:
        if (
            version is None
            or version.owner_id != owner_id
            or version.novel_id != novel_id
            or version.status not in allowed_statuses
            or version.source_snapshot_hash != build.source_snapshot_hash
            or version.hierarchy_build_id != build.build_id
            or version.hierarchy_checksum != build.manifest_checksum
            or (
                pointer_manifest is not None
                and version.manifest_checksum != pointer_manifest
            )
        ):
            return (ReasonCode.OPTIONAL_LINEAGE_MISMATCH,)
        return ()

    @staticmethod
    def _optional_unavailable(
        kind: AssetKind, owner_id: int, novel_id: int
    ) -> AssetInventory:
        return AssetInventory(
            kind=kind,
            owner_id=owner_id,
            novel_id=novel_id,
            available=False,
            reason_codes=(ReasonCode.SOURCE_UNAVAILABLE,),
        )

    @staticmethod
    def _optional_result(
        kind: AssetKind,
        owner_id: int,
        novel_id: int,
        version_id: str,
        reasons: tuple[ReasonCode, ...],
        *,
        item_count: int = 0,
    ) -> AssetInventory:
        return AssetInventory(
            kind=kind,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            item_count=item_count,
            healthy_empty=item_count == 0 and not reasons,
            available=not reasons,
            reason_codes=reasons,
        )

    @staticmethod
    def _coalesce_ranges(chapter_numbers: set[int]) -> tuple[RebuildRange, ...]:
        ordered = sorted(chapter_numbers)
        if not ordered:
            return ()
        ranges: list[RebuildRange] = []
        start = end = ordered[0]
        for number in ordered[1:]:
            if number == end + 1:
                end = number
                continue
            ranges.append(RebuildRange(start_chapter=start, end_chapter=end))
            start = end = number
        ranges.append(RebuildRange(start_chapter=start, end_chapter=end))
        return tuple(ranges)
