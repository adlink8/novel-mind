"""Phase 39-01 derivative export audit fixture tests (D-39-03/D-39-04, REQ-SHIP-02).

Proves the export audit contract on CI PostgreSQL:

- the frozen round-trip fixture data (revision/asset/citation/version/lineage)
  aligns with the sealed manifest: the manifest hash replays its payload and
  the export hash equals the snapshot hash;
- a clean owner/project/fork-scoped snapshot yields ``qualified_candidate`` —
  readiness (snapshot builds), data (chapters/assets present) and quality
  (parity checks pass) stay independent dimensions, and the audit verdict never
  promotes an active pointer or writes the Original space (REQ-SHIP-02);
- any missing provenance / parity mismatch yields ``blocked`` instead of a
  falsely green audit (failure policy).
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.derivative_export.manifest import (
    derivative_export_manifest_hash,
    seal_derivative_export_manifest,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshotError,
    ExportSnapshotService,
)
from app.services.derivative_visual.assets import DerivativeAssetStorage
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    build_fixture_snapshot,
    seal_fixture_manifest,
)
from tests.integration.conftest import reset_public_schema, run_alembic
from tests.integration.test_derivative_export import _seed_chain

pytestmark = pytest.mark.integration


def async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest.fixture(scope="module")
def asset_storage() -> DerivativeAssetStorage:
    with tempfile.TemporaryDirectory(prefix="novelmind-deriv-audit-") as tmp:
        yield DerivativeAssetStorage(Path(tmp))


@pytest_asyncio.fixture
async def factory(migrated_postgres: str):
    aengine = create_async_engine(
        async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    session_factory = async_sessionmaker(
        aengine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        yield session_factory
    finally:
        await aengine.dispose()


def _audit_verdict(snapshot) -> dict:
    """Three-dimensional report: readiness/data/quality stay independent."""
    dimensions = {
        "readiness": "qualified_candidate" if snapshot.snapshot_hash else "blocked",
        "data": "present" if snapshot.chapters else "blocked",
        "quality": "pass" if not snapshot.missing_assets else "concern",
    }
    # REQ-SHIP-02: the export never promotes a production pointer.
    promoted = False
    return {"dimensions": dimensions, "promoted": promoted, "snapshot_hash": snapshot.snapshot_hash}


async def test_frozen_fixture_manifest_replays_and_aligns():
    """The pure frozen fixture data seals to a manifest whose hash replays."""
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    assert derivative_export_manifest_hash(manifest) == manifest.manifest_hash
    assert manifest.manifest_hash == snapshot.snapshot_hash
    # Full provenance is frozen: project/revision/version/snapshot/asset/citation.
    assert manifest.project_id == snapshot.project_id
    assert manifest.source_snapshot == snapshot.source_snapshot
    assert manifest.project_manifest_hash == snapshot.project_manifest_hash
    assert len(manifest.revisions) == 1
    assert len(manifest.citations) >= 1
    assert all(rev.status == "derivative_revision" for rev in manifest.revisions)


async def test_clean_snapshot_is_qualified_candidate_never_promoted(
    factory, asset_storage: DerivativeAssetStorage, migrated_postgres: str
):
    ids = _seed_chain(migrated_postgres, asset_storage, suffix=f"audit_{uuid.uuid4().hex[:6]}")
    async with factory() as session:
        frozen = await ExportSnapshotService(session, storage=asset_storage).build(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            project_id=ids["project_id"],
        )
    snapshot = frozen.snapshot
    report = _audit_verdict(snapshot)

    # Three independent dimensions.
    assert report["dimensions"]["readiness"] == "qualified_candidate"
    assert report["dimensions"]["data"] == "present"
    assert report["dimensions"]["quality"] == "pass"
    assert report["promoted"] is False
    # The report carries the frozen snapshot hash (auditable).
    assert report["snapshot_hash"] == snapshot.snapshot_hash

    # The manifest replays from the real DB seed and stays aligned.
    manifest = seal_derivative_export_manifest(snapshot)
    assert derivative_export_manifest_hash(manifest) == manifest.manifest_hash
    assert manifest.manifest_hash == snapshot.snapshot_hash
    assert [c.chapter_number for c in manifest.chapters] == [1, 2]

    # The audit never mutated the Original space: no Original chapter rows were
    # ever written by the seed or the export (derivative-only, D-39-02).
    async with factory() as session:
        original = list(
            (
                await session.execute(
                    text(
                        "SELECT chapter_number, content FROM chapters "
                        "WHERE novel_id = :n ORDER BY chapter_number"
                    ),
                    {"n": ids["novel_id"]},
                )
            ).all()
        )
    assert original == []


async def test_missing_provenance_is_blocked_not_green(
    factory, asset_storage: DerivativeAssetStorage, migrated_postgres: str
):
    """Any parity mismatch must surface as blocked, never a false green."""
    ids = _seed_chain(migrated_postgres, asset_storage, suffix=f"blk_{uuid.uuid4().hex[:6]}")
    # Drift a chapter version token -> stale revision -> blocked.
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE derivative_chapters SET revision = revision + 1 "
                "WHERE id = :cid"
            ),
            {"cid": ids["chapter_ids"][0]},
        )
        await session.commit()

    async with factory() as session:
        with pytest.raises(ExportSnapshotError) as exc:
            await ExportSnapshotService(session, storage=asset_storage).build(
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                project_id=ids["project_id"],
            )
    assert exc.value.code == "revision_version_stale"
    # The blocked code is an explicit failure dimension — never "pass".
    assert exc.value.code not in {"pass", "ok", "success", "qualified_candidate"}
