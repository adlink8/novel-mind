"""Executable Phase 09 relationship release authority contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.run_relationship_qualification import (
    REQUIRED_TEST_COMMANDS,
    CommandSpec,
    run_production_qualification,
    run_release_verification,
    scope_scan,
)
from tests.integration.conftest import run_alembic
from tests.integration.relationships.test_api import _async_url, _seed_graph

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]


def _executed_command_specs(tmp_path: Path, *, failing_index: int | None = None):
    return tuple(
        CommandSpec(
            display=display,
            cwd=tmp_path,
            argv=(
                sys.executable,
                "-c",
                (
                    f"import sys; sys.stdout.write('rel-release-check-{index}'); "
                    f"raise SystemExit({9 if index == failing_index else 0})"
                ),
            ),
        )
        for index, display in enumerate(REQUIRED_TEST_COMMANDS)
    )


async def _qualified_report(empty_postgres: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine, with_future=True)
    engine.dispose()

    aengine = create_async_engine(_async_url(empty_postgres))
    sessions = async_sessionmaker(aengine, expire_on_commit=False)
    report = await run_production_qualification(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version_id=ids["v1_id"],
        sessions=sessions,
        repo_root=REPO_ROOT,
    )
    assert report["status"] == "qualified", report
    return aengine, sessions, ids, report


@pytest.mark.asyncio
async def test_production_qualification_binds_postgres_and_spoiler(
    empty_postgres: str, require_postgres: None
):
    aengine, sessions, ids, report = await _qualified_report(empty_postgres)
    assert report["report_version"] == "relationship-production-qualification.v1"
    assert report["artifact"]["database_dialect"] == "postgresql"
    assert report["artifact"]["counts"]["accepted_observations"] >= 1
    assert report["metrics"]["spoiler_leaks"] == 0
    assert report["gates"]["spoiler_safety"] is True
    assert report["gates"]["projection_replay"] is True
    assert report["gates"]["cytoscape_lock"] is True
    assert report["gates"]["scope_clean"] is True
    assert report["artifact"]["authority"]["version_id"] == ids["v1_id"]
    assert "CarolFuture" not in json.dumps(
        report["artifact"]["spoiler_observation"], ensure_ascii=False
    )
    await aengine.dispose()


@pytest.mark.asyncio
async def test_release_entry_qualifies_with_fresh_postgres_and_commands(
    empty_postgres: str, require_postgres: None, tmp_path: Path
):
    aengine, sessions, ids, report = await _qualified_report(empty_postgres)
    report_path = tmp_path / "relationship-qualification.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    observer_engine = create_async_engine(_async_url(empty_postgres))
    observer_sessions = async_sessionmaker(observer_engine, expire_on_commit=False)

    verdict = await run_release_verification(
        REPO_ROOT,
        report_path,
        sessions=observer_sessions,
        command_specs=_executed_command_specs(tmp_path),
    )

    assert verdict["status"] == "qualified", verdict
    assert verdict["quality_comparable"] is True
    assert all(item["exit_code"] == 0 for item in verdict["command_results"])
    assert all(len(item["output_sha256"]) == 64 for item in verdict["command_results"])
    assert len({item["output_sha256"] for item in verdict["command_results"]}) == len(
        REQUIRED_TEST_COMMANDS
    )
    # Public verdict must not leak captured command stdout bodies.
    assert all("output" not in item for item in verdict["command_results"])

    await observer_engine.dispose()
    await aengine.dispose()


@pytest.mark.asyncio
async def test_release_entry_blocks_failed_command(
    empty_postgres: str, require_postgres: None, tmp_path: Path
):
    aengine, sessions, ids, report = await _qualified_report(empty_postgres)
    report_path = tmp_path / "relationship-qualification.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    observer_engine = create_async_engine(_async_url(empty_postgres))
    observer_sessions = async_sessionmaker(observer_engine, expire_on_commit=False)

    verdict = await run_release_verification(
        REPO_ROOT,
        report_path,
        sessions=observer_sessions,
        command_specs=_executed_command_specs(tmp_path, failing_index=1),
    )

    assert verdict["status"] == "blocked_release", verdict
    assert verdict["quality_comparable"] is False
    assert verdict["checks"]["database_authority"] is True
    assert verdict["checks"]["command_output_attestation"] is False
    assert verdict["command_results"][1]["exit_code"] == 9

    await observer_engine.dispose()
    await aengine.dispose()


@pytest.mark.asyncio
async def test_release_entry_blocks_postgres_authority_mismatch(
    empty_postgres: str, require_postgres: None, tmp_path: Path
):
    aengine, sessions, ids, report = await _qualified_report(empty_postgres)
    # Tamper authority inside the sealed report without recomputing digests.
    report["artifact"]["authority"]["accepted_observation_count"] = 0
    report_path = tmp_path / "relationship-qualification.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    observer_engine = create_async_engine(_async_url(empty_postgres))
    observer_sessions = async_sessionmaker(observer_engine, expire_on_commit=False)

    verdict = await run_release_verification(
        REPO_ROOT,
        report_path,
        sessions=observer_sessions,
        command_specs=_executed_command_specs(tmp_path),
    )

    assert verdict["status"] == "blocked_release", verdict
    assert (
        verdict["checks"]["production_artifact_signature"] is False
        or verdict["checks"]["database_authority"] is False
    )

    await observer_engine.dispose()
    await aengine.dispose()


@pytest.mark.asyncio
async def test_release_entry_blocks_missing_or_malformed_report(
    empty_postgres: str, require_postgres: None, tmp_path: Path
):
    observer_engine = create_async_engine(_async_url(empty_postgres))
    observer_sessions = async_sessionmaker(observer_engine, expire_on_commit=False)

    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    verdict = await run_release_verification(
        REPO_ROOT,
        bad,
        sessions=observer_sessions,
        command_specs=_executed_command_specs(tmp_path),
    )
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"].get("well_formed_report") is False

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"status": "qualified"}), encoding="utf-8")
    verdict2 = await run_release_verification(
        REPO_ROOT,
        empty,
        sessions=observer_sessions,
        command_specs=_executed_command_specs(tmp_path),
    )
    assert verdict2["status"] == "blocked_release"

    await observer_engine.dispose()


def test_scope_scan_documents_phase_contracts_without_chat_or_clue():
    result = scope_scan(REPO_ROOT)
    assert result["phase10_contract_present"] is True
    assert result["phase11_contract_present"] is True
    assert result["forbidden_hits"] == []
    assert result["scope_clean"] is True
