"""Default-off offline experiment runner integration tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.services.narrative_memory.experiments import (
    ExperimentDisabledError,
    experiment_request_from_fixture,
    run_retrieval_experiment,
    sanitize_public_report,
)
from app.services.narrative_memory.retrieval_contracts import RetrievalRunStatus
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_retrieval_candidates_pg import (
    HEX_A,
    _seed_eligible_candidate,
)


pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[3]
CLI = BACKEND / "scripts" / "run_hierarchical_retrieval_experiment.py"


@pytest.fixture
async def exp_pg(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    sync = create_engine(empty_postgres)
    try:
        seed = _seed_eligible_candidate(sync)
    finally:
        sync.dispose()
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session, seed
            await session.rollback()
    finally:
        await engine.dispose()


def test_settings_default_disables_experiment():
    assert settings.narrative_memory_retrieval_experiment_enabled is False


@pytest.mark.asyncio
async def test_experiment_refuses_when_disabled(exp_pg):
    session, seed = exp_pg
    req = experiment_request_from_fixture(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
        raw_question="角色在哪里",
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
    )
    with pytest.raises(ExperimentDisabledError):
        await run_retrieval_experiment(session, req, enabled=False)


@pytest.mark.asyncio
async def test_enabled_experiment_produces_deterministic_report(exp_pg):
    session, seed = exp_pg
    req = experiment_request_from_fixture(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
        raw_question="角色在哪里",
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
        expected_manifest_checksum=HEX_A,
    )
    a = await run_retrieval_experiment(session, req, enabled=True)
    b = await run_retrieval_experiment(session, req, enabled=True)
    assert a.status is RetrievalRunStatus.COMPLETED
    assert a.exit_code == 0
    assert sanitize_public_report(a.report) == sanitize_public_report(b.report)
    assert a.report["qualification"] is None
    assert a.report["promotion"] is None
    assert "角色在哪里" not in a.canonical_json()
    assert a.report["manifest_checksum"]
    assert a.report["citation_count"] >= 0


@pytest.mark.asyncio
async def test_ineligible_version_blocks(exp_pg):
    session, seed = exp_pg
    req = experiment_request_from_fixture(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=999999,
        raw_question="角色在哪里",
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
    )
    result = await run_retrieval_experiment(session, req, enabled=True)
    assert result.status is RetrievalRunStatus.BLOCKED
    assert result.exit_code == 2


def test_cli_default_off_no_side_effects(empty_postgres: str):
    """CLI without enable flag must exit blocked without DB mutations."""

    run_alembic("upgrade", "head", database_url=empty_postgres)
    proc = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--owner-id",
            "1",
            "--novel-id",
            "1",
            "--version-id",
            "1",
            "--question",
            "test",
            "--cutoff-chapter",
            "1",
            "--cutoff-snapshot-hash",
            HEX_A,
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        check=False,
        env={
            **dict(__import__("os").environ),
            "NOVELMIND_NARRATIVE_MEMORY_RETRIEVAL_EXPERIMENT_ENABLED": "false",
        },
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "experiment_disabled"
