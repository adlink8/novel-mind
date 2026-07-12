"""Subprocess smoke coverage for every Phase 05 production CLI entrypoint."""

import subprocess
import sys
import os
from pathlib import Path

import pytest

# CLI subprocess smoke needs more than unit (5s) budget; classify as integration (30s).
pytestmark = pytest.mark.integration

BACKEND = Path(__file__).parents[1]
SCRIPTS = (
    "build_narrative_units.py",
    "build_narrative_unit_index.py",
    "run_narrative_unit_eval.py",
    "promote_narrative_unit_index.py",
    "refresh_narrative_units.py",
    "reconcile_narrative_unit_index.py",
    "rollback_narrative_unit_index.py",
)


@pytest.mark.parametrize("script", SCRIPTS)
def test_documented_cli_entrypoint_executes(script):
    result = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / script), "--help"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_documented_build_and_rollback_dry_runs_execute():
    commands = (
        [
            sys.executable,
            "scripts/build_narrative_unit_index.py",
            "--build-id",
            "1",
            "--dry-run",
        ],
        [
            sys.executable,
            "scripts/rollback_narrative_unit_index.py",
            "--journal-id",
            "TEST",
            "--dry-run",
        ],
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_exact_05_02_build_command_executes_against_frozen_snapshot(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.models.base import Base
    from tests.test_knowledge_unit_materialize import _accepted_source

    database = tmp_path / "cli-contract.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    search_vector = Base.metadata.tables["text_chunks"].c.search_vector
    postgres_computed = search_vector.computed
    search_vector.computed = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            snapshot = await _accepted_source(session)
            snapshot_id = snapshot.id
            await session.commit()
    finally:
        search_vector.computed = postgres_computed
        await engine.dispose()

    env = os.environ.copy()
    env["NOVELMIND_DATABASE_URL"] = (
        f"sqlite+aiosqlite:///{database.as_posix()}"
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_narrative_units.py",
            "--snapshot-id",
            str(snapshot_id),
            "--dry-run",
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = __import__("json").loads(result.stdout[result.stdout.index("{") :])
    assert payload["materialize"]["snapshot_id"] == snapshot_id
    assert payload["materialize"]["created"] == 1


@pytest.mark.parametrize(
    "script",
    ("promote_narrative_unit_index.py", "refresh_narrative_units.py"),
)
def test_production_cli_rejects_evidence_secret_override(script):
    result = subprocess.run(
        [sys.executable, f"scripts/{script}", "--evidence-secret", "attacker-key"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "evidence-secret" in result.stderr


@pytest.mark.parametrize(
    "command",
    (
        (
            "scripts/promote_narrative_unit_index.py",
            "--prepare",
            "--checksum",
            "checksum",
        ),
        (
            "scripts/refresh_narrative_units.py",
            "--owner-id",
            "1",
            "--novel-id",
            "1",
            "--snapshot-id",
            "1",
            "--fixture",
            "fixture.json",
            "--approved-by",
            "owner",
        ),
    ),
)
def test_production_cli_requires_environment_signing_secret(command):
    env = os.environ.copy()
    env.pop("NARRATIVE_EVAL_SIGNING_SECRET", None)
    result = subprocess.run(
        [sys.executable, *command],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "NARRATIVE_EVAL_SIGNING_SECRET is required" in result.stderr
