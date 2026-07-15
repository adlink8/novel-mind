"""PostgreSQL integration tests for relationship projection replay."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.relationship import RelationshipObservation, RelationshipProjectionAudit
from app.services.relationships.projection import (
    ProjectionConfig,
    RelationshipProjectionService,
    replay_accepted_observations,
    sha256_canonical,
)
from tests.integration.relationships.test_api import _async_session, _seed_graph
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_projection_manifest_checksum_stable_and_empty_equal(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine, with_future=False)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipProjectionService()
    async with factory() as db:
        m1 = await svc.build_manifest(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v1_id"],
        )
        m2 = await svc.build_manifest(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v1_id"],
        )
        assert m1["manifest_checksum"] == m2["manifest_checksum"]
        assert m1["manifest_checksum"] == sha256_canonical(
            {k: v for k, v in m1.items() if k != "manifest_checksum"}
        )
        assert len(m1["observations"]) >= 1

        # Empty version (no observations on a fresh version key is harder; use foreign empty).
        empty = await svc.build_manifest(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v2_id"],
        )
        # v2 has candidate_obs only when with_future default; with_future=False still seeds candidate.
        empty2 = await svc.build_manifest(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v2_id"],
        )
        assert empty["manifest_checksum"] == empty2["manifest_checksum"]

        r1 = await replay_accepted_observations(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v1_id"],
            config=ProjectionConfig(enabled=False),
        )
        await db.commit()
        r2 = await replay_accepted_observations(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v1_id"],
            config=ProjectionConfig(enabled=False),
        )
        await db.commit()
        assert r1.manifest_checksum == r2.manifest_checksum
        assert r1.status == "disabled"
        assert r2.status == "disabled"

    await aengine.dispose()


class _BoomAdapter:
    def project(self, manifest: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("simulated_neo4j_failure")


@pytest.mark.asyncio
async def test_projection_failure_does_not_mutate_observation_status(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine, with_future=False)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    async with factory() as db:
        before = list(
            (
                await db.scalars(
                    select(RelationshipObservation).where(
                        RelationshipObservation.analysis_version_id == ids["v1_id"]
                    )
                )
            ).all()
        )
        snapshots = [
            (
                row.id,
                row.status,
                row.observation_checksum,
                row.relation_type,
                row.confidence,
            )
            for row in before
        ]

        result = await replay_accepted_observations(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v1_id"],
            config=ProjectionConfig(enabled=True),
            adapter=_BoomAdapter(),
        )
        await db.commit()
        assert result.status == "failed"
        assert result.manifest_checksum
        assert "simulated_neo4j_failure" in (result.reason or "")

        after = list(
            (
                await db.scalars(
                    select(RelationshipObservation).where(
                        RelationshipObservation.analysis_version_id == ids["v1_id"]
                    )
                )
            ).all()
        )
        after_map = {
            row.id: (
                row.id,
                row.status,
                row.observation_checksum,
                row.relation_type,
                row.confidence,
            )
            for row in after
        }
        for snap in snapshots:
            assert after_map[snap[0]] == snap
            assert after_map[snap[0]][1] == "accepted"

        audits = list(
            (
                await db.scalars(
                    select(RelationshipProjectionAudit).where(
                        RelationshipProjectionAudit.analysis_version_id == ids["v1_id"]
                    )
                )
            ).all()
        )
        assert any(a.status == "failed" for a in audits)

    await aengine.dispose()


@pytest.mark.asyncio
async def test_replay_checksum_identical_for_same_manifest(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipProjectionService()
    async with factory() as db:
        r1 = await svc.replay_accepted_observations(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v1_id"],
        )
        await db.commit()
        r2 = await svc.replay_accepted_observations(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=ids["v1_id"],
        )
        await db.commit()
        assert r1.manifest_checksum == r2.manifest_checksum
        assert r1.observation_count == r2.observation_count

    await aengine.dispose()
