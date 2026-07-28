"""PostgreSQL integration tests for spoiler-safe context manifests (10-03)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisVersion
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.novel import Chapter, Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineEvidenceRef,
)
from app.models.user import User
from app.schemas.reader_chat import SelectionCoordinate
from app.services.reader_chat.context import (
    SELECTION_EVIDENCE_KEY,
    assemble_context_manifest,
    freeze_manifest_from_stored,
    validate_selection,
)
from app.services.reader_chat.retrieval import (
    RelationshipObservationEvidence,
    RelationshipObservationItem,
    SourceStatus,
)
from app.services.timeline.query import resolve_chapter_cutoff
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64
HEX64_E = "e" * 64
HEX64_F = "f" * 64


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_context_graph(engine, *, with_future: bool = True) -> dict[str, Any]:
    """Seed owner/novel/chapters/hierarchy/timeline with visible + future evidence."""

    with Session(engine) as session:
        owner = User(
            username="ctx_owner",
            email="ctx_owner@example.test",
            hashed_password="hash",
        )
        session.add(owner)
        session.flush()

        novel = Novel(
            title="Context Novel",
            owner_id=owner.id,
            status="ready",
            reading_progress={},
        )
        session.add(novel)
        session.flush()

        ch1_content = "第一章可见：龙与精灵结盟于桥上。重复重复。"
        ch2_content = "第二章秘密：魔王真实身份揭晓。"
        ch1 = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content=ch1_content,
            word_count=len(ch1_content),
        )
        ch2 = Chapter(
            novel_id=novel.id,
            chapter_number=2,
            title="第二章",
            content=ch2_content,
            word_count=len(ch2_content),
        )
        session.add_all([ch1, ch2])
        session.flush()

        build_id = "ctx-build-1"
        build = ChunkBuild(
            build_id=build_id,
            novel_id=novel.id,
            status="active",
            source_snapshot_hash=HEX64,
            manifest_checksum=HEX64_B,
            chunker_name="test",
            chunker_version="1",
            chunker_config_hash=HEX64_C,
            collection_name="ctx-col",
            is_candidate=False,
            immutable=True,
            changed_chapter_ids=[],
            journal=[],
            vector_ids=[],
        )
        session.add(build)
        session.flush()
        session.add(
            ChunkActivePointer(
                novel_id=novel.id,
                build_id=build_id,
                committed_at=datetime.now(timezone.utc),
            )
        )

        vis_node = ChunkHierarchyNode(
            build_id=build_id,
            novel_id=novel.id,
            node_id="ev-vis-1",
            level="evidence",
            chapter_id=ch1.id,
            chapter_number=1,
            content=ch1_content[0:10],
            content_hash=_sha(ch1_content[0:10]),
            source_start=0,
            source_end=10,
            order_index=0,
        )
        fut_node = ChunkHierarchyNode(
            build_id=build_id,
            novel_id=novel.id,
            node_id="ev-fut-1",
            level="evidence",
            chapter_id=ch2.id,
            chapter_number=2,
            content=ch2_content[0:10],
            content_hash=_sha(ch2_content[0:10]),
            source_start=0,
            source_end=10,
            order_index=1,
        )
        session.add_all([vis_node, fut_node])

        version = AnalysisVersion(
            owner_id=owner.id,
            novel_id=novel.id,
            version_key="v1",
            status="active",
            source_snapshot_hash=HEX64,
            hierarchy_build_id=build_id,
            hierarchy_checksum=HEX64_B,
            prompt_hash=HEX64_C,
            schema_hash=HEX64_D,
            model_lineage={},
            decoding_hash=HEX64_E,
            config_hash=HEX64_F,
            price_snapshot={},
            manifest={},
        )
        session.add(version)
        session.flush()
        session.add(
            TimelineActivePointer(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                revision=1,
                manifest_checksum=HEX64,
            )
        )

        vis_event = MachineTimelineEvent(
            version_id=version.id,
            owner_id=owner.id,
            novel_id=novel.id,
            logical_event_id="vis-event",
            title="可见事件",
            description="第一章结盟",
            event_type="plot",
            time_precision="unknown",
            narrative_chapter_number=1,
            narrative_index=0,
            story_rank=1,
            story_constraints=[],
            confidence=0.9,
            prompt_hash=HEX64_C,
            schema_hash=HEX64_D,
            model_lineage={},
            publication_status="published",
        )
        fut_event = MachineTimelineEvent(
            version_id=version.id,
            owner_id=owner.id,
            novel_id=novel.id,
            logical_event_id="fut-event",
            title="未来事件",
            description="魔王身份",
            event_type="plot",
            time_precision="unknown",
            narrative_chapter_number=2,
            narrative_index=0,
            story_rank=2,
            story_constraints=[],
            confidence=0.9,
            prompt_hash=HEX64_C,
            schema_hash=HEX64_D,
            model_lineage={},
            publication_status="published",
        )
        session.add_all([vis_event, fut_event])
        session.flush()
        session.add_all(
            [
                TimelineEvidenceRef(
                    event_id=vis_event.id,
                    chapter_id=ch1.id,
                    evidence_id="tl-vis",
                    source_start=0,
                    source_end=8,
                    content_hash=_sha(ch1_content[0:8]),
                ),
                TimelineEvidenceRef(
                    event_id=fut_event.id,
                    chapter_id=ch2.id,
                    evidence_id="tl-fut",
                    source_start=0,
                    source_end=8,
                    content_hash=_sha(ch2_content[0:8]),
                ),
            ]
        )
        session.commit()

        return {
            "owner_id": owner.id,
            "novel_id": novel.id,
            "ch1_id": ch1.id,
            "ch2_id": ch2.id,
            "ch1_content": ch1_content,
            "ch2_content": ch2_content,
            "version_id": version.id,
            "build_id": build_id,
            "build_checksum": HEX64_B,
            "with_future": with_future,
        }


def _async_url(sync_url: str) -> str:
    if "+asyncpg" in sync_url:
        return sync_url
    if "+psycopg2" in sync_url:
        return sync_url.replace("+psycopg2", "+asyncpg")
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


async def _async_session(database_url: str):
    engine = create_async_engine(_async_url(database_url), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


class FakeRelationshipReader:
    """Fake Phase 09 reader returning both visible and future observations."""

    def __init__(
        self, owner_id: int, novel_id: int, version_id: int, ch1_id: int, ch2_id: int
    ):
        self.owner_id = owner_id
        self.novel_id = novel_id
        self.version_id = version_id
        self.ch1_id = ch1_id
        self.ch2_id = ch2_id
        self.calls = 0

    async def list_visible_observations(
        self,
        session,
        *,
        novel,
        owner_id: int,
        version_id: int,
        through_chapter: int | None,
        request_full_book: bool = False,
    ) -> list[RelationshipObservationItem]:
        self.calls += 1
        # Reader itself is expected to pre-filter; we intentionally return both so
        # Phase 10 revalidation is the last line of defense.
        items = [
            RelationshipObservationItem(
                observation_id=101,
                analysis_version_id=version_id,
                owner_id=owner_id,
                novel_id=novel.id,
                source_character_id=1,
                target_character_id=2,
                relation_type="ally",
                valid_from_chapter=1,
                valid_to_chapter=None,
                status="accepted",
                evidence=(
                    RelationshipObservationEvidence(
                        evidence_id="rel-vis",
                        chapter_id=self.ch1_id,
                        source_start=0,
                        source_end=6,
                        content_hash=_sha("visible"),
                        chapter_number=1,
                        excerpt="结盟",
                    ),
                ),
            ),
            RelationshipObservationItem(
                observation_id=202,
                analysis_version_id=version_id,
                owner_id=owner_id,
                novel_id=novel.id,
                source_character_id=1,
                target_character_id=3,
                relation_type="enemy",
                valid_from_chapter=2,
                valid_to_chapter=None,
                status="accepted",
                evidence=(
                    RelationshipObservationEvidence(
                        evidence_id="rel-fut",
                        chapter_id=self.ch2_id,
                        source_start=0,
                        source_end=6,
                        content_hash=_sha("future"),
                        chapter_number=2,
                        excerpt="魔王",
                    ),
                ),
            ),
        ]
        if request_full_book:
            return items
        if through_chapter is None:
            return items
        return [i for i in items if i.valid_from_chapter <= through_chapter]


@pytest.mark.asyncio
async def test_no_progress_defaults_to_first_chapter_and_excludes_future(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_context_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    try:
        async with factory() as db:
            novel = await db.get(Novel, ids["novel_id"])
            assert novel is not None
            cutoff = await resolve_chapter_cutoff(db, novel)
            assert cutoff == 1

            content = ids["ch1_content"]
            selection = await validate_selection(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=SelectionCoordinate(
                    chapter_id=ids["ch1_id"],
                    source_start=0,
                    source_end=6,
                    selection_text=content[0:6],
                    selection_text_hash=_sha(content[0:6]),
                    chapter_content_hash=_sha(content),
                ),
            )
            reader = FakeRelationshipReader(
                ids["owner_id"],
                ids["novel_id"],
                ids["version_id"],
                ids["ch1_id"],
                ids["ch2_id"],
            )
            manifest = await assemble_context_manifest(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=selection,
                question="他们为什么结盟？",
                prior_dialogue=[{"role": "user", "body": "旧问", "sequence": 1}],
                relationship_reader=reader,
            )

            keys = [e.evidence_key for e in manifest.evidence]
            assert SELECTION_EVIDENCE_KEY in keys
            assert any(k.startswith("hierarchy:") for k in keys)
            assert any(k.startswith("timeline:") for k in keys)
            assert any(k.startswith("relationship_observation:101") for k in keys)

            # Future side channels must not enter the canonical graph.
            blob = str(manifest.canonical_payload())
            assert "ev-fut-1" not in blob
            assert "fut-event" not in blob
            assert "rel-fut" not in blob
            assert "魔王" not in blob
            assert "未来事件" not in blob
            assert "第二章秘密" not in blob
            assert manifest.cutoff_chapter_number == 1
            assert manifest.full_book is False
            assert all(e.chapter_number <= 1 for e in manifest.evidence)

            framing = manifest.prompt_inputs["dialogue_framing"]
            assert framing["is_evidence"] is False
            assert framing["label"] == "CONVERSATIONAL_FRAMING_NOT_EVIDENCE"
            # Prior dialogue bodies are not evidence and not stored raw.
            assert "旧问" not in str(framing)
            assert SELECTION_EVIDENCE_KEY in manifest.allowed_evidence_ids()
            assert "旧问" not in manifest.allowed_evidence_ids()

            # Deterministic checksum
            m2 = await assemble_context_manifest(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=selection,
                question="他们为什么结盟？",
                prior_dialogue=[{"role": "user", "body": "旧问", "sequence": 1}],
                relationship_reader=reader,
            )
            assert m2.manifest_checksum == manifest.manifest_checksum
    finally:
        await aengine.dispose()


@pytest.mark.asyncio
async def test_full_book_only_when_persisted_timeline_full_book(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_context_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    try:
        async with factory() as db:
            novel = await db.get(Novel, ids["novel_id"])
            assert novel is not None
            # Requesting full book without preference still cut off at progress.
            novel.reading_progress = {
                "chapter_id": ids["ch1_id"],
                "timeline_full_book": False,
            }
            await db.commit()
            await db.refresh(novel)

            content = ids["ch1_content"]
            selection = await validate_selection(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=SelectionCoordinate(
                    chapter_id=ids["ch1_id"],
                    source_start=0,
                    source_end=4,
                    selection_text=content[0:4],
                    selection_text_hash=_sha(content[0:4]),
                    chapter_content_hash=_sha(content),
                ),
            )
            reader = FakeRelationshipReader(
                ids["owner_id"],
                ids["novel_id"],
                ids["version_id"],
                ids["ch1_id"],
                ids["ch2_id"],
            )
            denied = await assemble_context_manifest(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=selection,
                relationship_reader=reader,
            )
            assert denied.full_book is False
            assert all(e.chapter_number <= 1 for e in denied.evidence)
            assert "rel-fut" not in str(denied.canonical_payload())

            novel.reading_progress = {
                "chapter_id": ids["ch1_id"],
                "timeline_full_book": True,
            }
            await db.commit()
            await db.refresh(novel)

            allowed = await assemble_context_manifest(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=selection,
                relationship_reader=reader,
            )
            assert allowed.full_book is True
            keys = [e.evidence_key for e in allowed.evidence]
            assert any("ev-fut-1" in k or "hierarchy:ev-fut" in k for k in keys)
            assert any(
                "rel-fut" in k or "relationship_observation:202" in k for k in keys
            )
            assert allowed.manifest_checksum != denied.manifest_checksum
    finally:
        await aengine.dispose()


@pytest.mark.asyncio
async def test_retry_rehydrates_original_checksum_not_current_progress(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_context_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    try:
        async with factory() as db:
            novel = await db.get(Novel, ids["novel_id"])
            assert novel is not None
            content = ids["ch1_content"]
            selection = await validate_selection(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=SelectionCoordinate(
                    chapter_id=ids["ch1_id"],
                    source_start=0,
                    source_end=5,
                    selection_text=content[0:5],
                    selection_text_hash=_sha(content[0:5]),
                    chapter_content_hash=_sha(content),
                ),
            )
            reader = FakeRelationshipReader(
                ids["owner_id"],
                ids["novel_id"],
                ids["version_id"],
                ids["ch1_id"],
                ids["ch2_id"],
            )
            original = await assemble_context_manifest(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=selection,
                relationship_reader=reader,
            )
            stored_checksum = original.manifest_checksum

            # Progress advances after send — rebuild would widen; retry must not.
            novel.reading_progress = {
                "chapter_id": ids["ch2_id"],
                "timeline_full_book": True,
            }
            await db.commit()
            await db.refresh(novel)

            rebuilt = await assemble_context_manifest(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=selection,
                relationship_reader=reader,
            )
            assert rebuilt.manifest_checksum != stored_checksum

            frozen = freeze_manifest_from_stored(
                reading_progress_snapshot=original.reading_progress_snapshot,
                full_book=original.full_book,
                cutoff_chapter_number=original.cutoff_chapter_number,
                analysis_version_id=original.analysis_version_id,
                hierarchy_build_id=original.hierarchy_build_id,
                hierarchy_checksum=original.hierarchy_checksum,
                evidence=[e.canonical_dict() for e in original.evidence],
                omitted_evidence_counts=original.omitted_evidence_counts,
                prompt_inputs=original.prompt_inputs,
                source_status=original.source_status,
                expected_checksum=stored_checksum,
            )
            assert frozen.manifest_checksum == stored_checksum
            assert frozen.full_book is False
            assert all(e.chapter_number <= 1 for e in frozen.evidence)
    finally:
        await aengine.dispose()


@pytest.mark.asyncio
async def test_relationship_source_unavailable_is_explicit(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_context_graph(engine)
    engine.dispose()

    class BoomReader:
        async def list_visible_observations(self, *args, **kwargs):
            raise RuntimeError("phase09_down")

    aengine, factory = await _async_session(empty_postgres)
    try:
        async with factory() as db:
            novel = await db.get(Novel, ids["novel_id"])
            content = ids["ch1_content"]
            selection = await validate_selection(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=SelectionCoordinate(
                    chapter_id=ids["ch1_id"],
                    source_start=0,
                    source_end=3,
                    selection_text=content[0:3],
                    selection_text_hash=_sha(content[0:3]),
                    chapter_content_hash=_sha(content),
                ),
            )
            manifest = await assemble_context_manifest(
                db,
                novel=novel,
                owner_id=ids["owner_id"],
                selection=selection,
                relationship_reader=BoomReader(),
            )
            assert (
                manifest.source_status["relationship_observation"]
                == SourceStatus.UNAVAILABLE
            )
            # Remaining evidence still present; no invented relationships.
            assert not any(
                e.source_type == "relationship_observation" for e in manifest.evidence
            )
            assert any(e.source_type == "selection" for e in manifest.evidence)
    finally:
        await aengine.dispose()
