"""Fresh-observer proof that Phase 13 candidate lifecycle never mutates production pointers."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chunk_build import ChunkActivePointer
from app.models.clue import ClueActivePointer
from app.models.eval import ActiveBaseline
from app.models.knowledge_unit import NarrativeActivePointer
from app.models.timeline import TimelineActivePointer
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.manifests import seal_and_report
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_provenance_pg import (
    _package_for_version,
    _seed_exact,
    _spec,
)


pytestmark = [pytest.mark.integration]


POINTER_TABLES = (
    "chunk_active_pointers",
    "timeline_active_pointers",
    "clue_active_pointers",
    "narrative_active_pointers",
    "active_baselines",
)


async def _pointer_snapshot(session: AsyncSession) -> dict[str, object]:
    chunk = tuple(
        (row.novel_id, row.build_id, str(row.committed_at))
        for row in (
            await session.scalars(
                select(ChunkActivePointer).order_by(ChunkActivePointer.novel_id)
            )
        ).all()
    )
    timeline = tuple(
        (row.owner_id, row.novel_id, row.version_id, row.revision, row.manifest_checksum)
        for row in (
            await session.scalars(
                select(TimelineActivePointer).order_by(TimelineActivePointer.id)
            )
        ).all()
    )
    clue = tuple(
        (row.owner_id, row.novel_id, row.version_id, row.revision)
        for row in (
            await session.scalars(
                select(ClueActivePointer).order_by(ClueActivePointer.id)
            )
        ).all()
    )
    narrative = tuple(
        (
            row.owner_id,
            row.novel_id,
            row.domain_profile,
            row.build_id,
            row.pointer_version,
        )
        for row in (
            await session.scalars(
                select(NarrativeActivePointer).order_by(NarrativeActivePointer.id)
            )
        ).all()
    )
    baselines = tuple(
        (row.owner_id, row.candidate_id, row.quality_run_id, row.chunk_manifest_hash)
        for row in (
            await session.scalars(select(ActiveBaseline).order_by(ActiveBaseline.id))
        ).all()
    )
    return {
        "chunk_active_pointers": chunk,
        "timeline_active_pointers": timeline,
        "clue_active_pointers": clue,
        "narrative_active_pointers": narrative,
        "active_baselines": baselines,
    }


@pytest.mark.asyncio
async def test_fresh_observer_sees_byte_equivalent_pointers_after_candidate_lifecycle(
    empty_postgres: str, pg_async_url: str
):
    from app.models.novel import Novel
    from app.models.user import User
    from app.services.narrative_memory.audit import audit_assets
    from app.services.narrative_memory.audit_pg import PostgresAuditSource

    run_alembic("upgrade", "head", database_url=empty_postgres)
    writer_engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    observer_engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    writer_factory = async_sessionmaker(
        writer_engine, class_=AsyncSession, expire_on_commit=False
    )
    observer_factory = async_sessionmaker(
        observer_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with observer_engine.connect() as conn:
            table_names = set(
                await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            )
            assert "narrative_memory_active_pointers" not in table_names
            for name in POINTER_TABLES:
                assert name in table_names

        async with writer_factory() as writer:
            user, novel, _report = await _seed_exact(writer)
            await writer.commit()
            owner_id, novel_id = user.id, novel.id

        async with observer_factory() as observer:
            before = await _pointer_snapshot(observer)

        async with writer_factory() as writer:
            user = await writer.scalar(select(User).where(User.id == owner_id))
            novel = await writer.scalar(select(Novel).where(Novel.id == novel_id))
            assert user is not None and novel is not None
            report = await audit_assets(
                PostgresAuditSource(writer), owner_id=owner_id, novel_id=novel_id
            )
            authority = CandidateAuthority(writer)
            version = await authority.create_version(
                owner_id=owner_id,
                novel_id=novel_id,
                spec=_spec(),
                eligibility_report=report,
            )
            package = await _package_for_version(writer, version)
            await authority.persist_package(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version.id,
                package=package,
            )
            result = await seal_and_report(
                writer,
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version.id,
            )
            await writer.commit()
            assert result.manifest_checksum
            assert result.report.verdict in {"qualified_candidate", "blocked"}

        async with observer_factory() as observer:
            after = await _pointer_snapshot(observer)
            assert after == before
            count = await observer.scalar(
                text("SELECT count(*) FROM narrative_memory_versions")
            )
            assert count == 1
            manifest_count = await observer.scalar(
                text("SELECT count(*) FROM narrative_memory_manifests")
            )
            assert manifest_count == 1
    finally:
        await writer_engine.dispose()
        await observer_engine.dispose()


def test_static_package_has_zero_provider_calls_and_no_pointer_api() -> None:
    """Phase 13 write/seal package has no provider/pointer/chat capability.

    Phase 12 audit helpers intentionally expose provider_calls_allowed and are
    out of this package scan.
    """

    root = (
        Path(__file__).resolve().parents[3]
        / "app"
        / "services"
        / "narrative_memory"
    )
    package_files = (
        "authority.py",
        "contracts.py",
        "provenance.py",
        "manifests.py",
    )
    forbidden = (
        "model_gateway",
        "worker_dispatch",
        "promote_",
        "rollback_",
        "reader_chat",
        "chromadb",
        "narrative_memory_active_pointers",
        "active_pointer",
        "resolve_current",
        "resolve_active",
    )
    for name in package_files:
        source = (root / name).read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in source, f"{name} contains {token}"
