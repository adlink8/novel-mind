from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.clue import ClueActivePointer, CluePointerJournal
from app.models.relationship import RelationshipBuildRun
from app.models.timeline import TimelineActivePointer, TimelinePointerJournal
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from scripts.run_asset_audit import collect_report
from tests.integration.narrative_memory.test_audit_pg import _seed_valid_hierarchy

pytestmark = pytest.mark.integration


async def _authority_snapshot(session) -> dict[str, object]:
    models = (
        ChunkBuild,
        ChunkHierarchyNode,
        RelationshipBuildRun,
        TimelinePointerJournal,
        CluePointerJournal,
    )
    counts = {
        model.__tablename__: await session.scalar(select(func.count()).select_from(model))
        for model in models
    }
    chunk_pointers = tuple(
        (row.novel_id, row.build_id)
        for row in (
            await session.scalars(
                select(ChunkActivePointer).order_by(ChunkActivePointer.novel_id)
            )
        ).all()
    )
    timeline_pointers = tuple(
        (row.owner_id, row.novel_id, row.version_id, row.revision)
        for row in (
            await session.scalars(
                select(TimelineActivePointer).order_by(TimelineActivePointer.id)
            )
        ).all()
    )
    clue_pointers = tuple(
        (row.owner_id, row.novel_id, row.version_id, row.revision)
        for row in (
            await session.scalars(select(ClueActivePointer).order_by(ClueActivePointer.id))
        ).all()
    )
    return {
        "counts": counts,
        "chunk_pointers": chunk_pointers,
        "timeline_pointers": timeline_pointers,
        "clue_pointers": clue_pointers,
    }


@pytest.mark.asyncio
async def test_service_and_cli_helpers_leave_authority_unchanged(audit_pg_session):
    owner, novel, _ = await _seed_valid_hierarchy(audit_pg_session)
    before = await _authority_snapshot(audit_pg_session)

    service_report = await audit_assets(
        PostgresAuditSource(audit_pg_session), owner_id=owner.id, novel_id=novel.id
    )
    cli_report = await collect_report(
        owner_id=owner.id, novel_id=novel.id, session=audit_pg_session
    )
    after = await _authority_snapshot(audit_pg_session)

    assert service_report == cli_report
    assert after == before


def test_entrypoints_and_audit_package_have_no_provider_or_write_capability() -> None:
    backend = Path(__file__).parents[3]
    files = [
        *sorted((backend / "app" / "services" / "narrative_memory").glob("*.py")),
        backend / "app" / "api" / "asset_audit.py",
        backend / "scripts" / "run_asset_audit.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in files)
    forbidden = (
        "litellm",
        "model_gateway",
        "ai_service",
        "set_active_pointer",
        "from app.services.chunking.promotion",
        "self._session.add(",
        "self._session.delete(",
        "self._session.commit(",
        "self._session.flush(",
        "from sqlalchemy import update",
        "from sqlalchemy import insert",
    )
    assert not [token for token in forbidden if token in text]
