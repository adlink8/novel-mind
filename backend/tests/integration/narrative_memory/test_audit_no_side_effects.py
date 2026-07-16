from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_db
from app.core.security import require_user
from app.main import app
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
        "builds": tuple(
            (row.build_id, row.novel_id, row.status, row.immutable, row.is_candidate, row.manifest_checksum)
            for row in (
                await session.scalars(select(ChunkBuild).order_by(ChunkBuild.id))
            ).all()
        ),
        "nodes": tuple(
            (
                row.build_id,
                row.novel_id,
                row.node_id,
                row.content_hash,
                row.source_start,
                row.source_end,
            )
            for row in (
                await session.scalars(
                    select(ChunkHierarchyNode).order_by(ChunkHierarchyNode.id)
                )
            ).all()
        ),
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


@pytest.mark.asyncio
async def test_fresh_observer_wraps_real_api_and_cli_for_exact_and_blocked_cases(
    asgi_client, audit_pg_session, pg_async_url
):
    owner, novel, _ = await _seed_valid_hierarchy(audit_pg_session)
    owner.is_superuser = True
    await audit_pg_session.commit()
    owner_id, novel_id = owner.id, novel.id
    factory = async_sessionmaker(
        audit_pg_session.bind, class_=AsyncSession, expire_on_commit=False
    )

    async def override_db():
        async with factory() as session:
            yield session

    async def as_admin():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_user] = as_admin

    async def fresh_snapshot() -> dict[str, object]:
        async with factory() as observer:
            return await _authority_snapshot(observer)

    def run_cli() -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["NOVELMIND_DATABASE_URL"] = pg_async_url
        env["NOVELMIND_DEBUG"] = "true"
        return subprocess.run(
            [
                sys.executable,
                "scripts/run_asset_audit.py",
                "--owner-id",
                str(owner_id),
                "--novel-id",
                str(novel_id),
            ],
            cwd=Path(__file__).parents[3],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    before = await fresh_snapshot()
    response = await asgi_client.get(
        f"/api/admin/asset-audit/{novel_id}", params={"owner_id": owner_id}
    )
    exact_cli = run_cli()
    after = await fresh_snapshot()
    assert response.status_code == 200
    assert response.json()["provider_calls_allowed"] is True
    assert exact_cli.returncode == 0, exact_cli.stdout
    assert after == before

    async with factory() as setup:
        build = await setup.scalar(
            select(ChunkBuild)
            .join(ChunkActivePointer, ChunkActivePointer.build_id == ChunkBuild.build_id)
            .where(ChunkActivePointer.novel_id == novel_id)
        )
        build.immutable = False
        await setup.commit()

    blocked_before = await fresh_snapshot()
    blocked_response = await asgi_client.get(
        f"/api/admin/asset-audit/{novel_id}", params={"owner_id": owner_id}
    )
    blocked_cli = run_cli()
    blocked_after = await fresh_snapshot()
    assert blocked_response.status_code == 200
    assert blocked_response.json()["provider_calls_allowed"] is False
    assert blocked_cli.returncode == 2, blocked_cli.stdout
    assert blocked_after == blocked_before


def test_entrypoints_and_audit_package_have_no_provider_or_write_capability() -> None:
    """Phase 12 audit surface only — later phases add builder/write modules in the same package."""
    backend = Path(__file__).parents[3]
    nm = backend / "app" / "services" / "narrative_memory"
    files = [
        *sorted(nm.glob("audit*.py")),
        backend / "app" / "api" / "asset_audit.py",
        backend / "scripts" / "run_asset_audit.py",
    ]
    assert files, "expected Phase 12 audit modules"
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
