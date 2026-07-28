"""Production-backed Phase 11 clue qualification and release contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.run_clue_qualification import (
    REQUIRED_TEST_COMMANDS,
    CommandSpec,
    run_offline_qualification,
    run_production_qualification,
    run_release_verification,
    scope_scan,
)

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
                    f"import sys; sys.stdout.write('clue-release-check-{index}'); "
                    f"raise SystemExit({9 if index == failing_index else 0})"
                ),
            ),
        )
        for index, display in enumerate(REQUIRED_TEST_COMMANDS)
    )


async def _qualified_report(pg_async_url: str):
    engine = create_async_engine(pg_async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    report = await run_production_qualification(sessions=sessions, repo_root=REPO_ROOT)
    assert report["status"] == "qualified", report
    return engine, sessions, report


@pytest.mark.asyncio
async def test_offline_qualification_is_fiction_and_lineage_bound():
    report = run_offline_qualification()
    assert report["status"] == "qualified"
    assert report["domain"] == "fiction"
    assert report["quality_comparable"] is True
    assert report["metrics"]["critical"]["false_active"] == 0
    assert report["metrics"]["critical"]["false_paid_off"] == 0
    assert report["lineage"]["fixture_sha256"]


@pytest.mark.asyncio
async def test_production_qualification_binds_postgres_and_spoiler(
    pg_async_url: str, require_postgres: None
):
    engine, sessions, report = await _qualified_report(pg_async_url)
    assert report["report_version"] == "clue-production-qualification.v1"
    assert report["artifact"]["database_dialect"] == "postgresql"
    assert report["artifact"]["counts"]["machine_clues"] >= 1
    assert report["artifact"]["counts"]["lifecycle_events"] >= 1
    assert report["metrics"]["spoiler_leaks"] == 0
    assert report["gates"]["spoiler_safety"] is True
    assert report["gates"]["paid_off_after_full_book"] is True
    assert report["gates"]["default_not_paid_off"] is True
    assert "SECRET FUTURE CLUE" not in json.dumps(
        report["artifact"]["spoiler_observation"], ensure_ascii=False
    )
    authority = report["artifact"]["authority"]
    assert authority["run_status"] == "completed"
    assert authority["lifecycle_count"] >= 1
    assert authority["evidence_count"] >= 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_release_entry_qualifies_with_fresh_postgres_and_commands(
    pg_async_url: str, require_postgres: None, tmp_path: Path
):
    engine, sessions, report = await _qualified_report(pg_async_url)
    report_path = tmp_path / "clue-qualification.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    observer_engine = create_async_engine(pg_async_url)
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
    assert all("output" not in item for item in verdict["command_results"])

    await observer_engine.dispose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_release_entry_blocks_failed_command(
    pg_async_url: str, require_postgres: None, tmp_path: Path
):
    engine, sessions, report = await _qualified_report(pg_async_url)
    report_path = tmp_path / "clue-qualification.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    observer_engine = create_async_engine(pg_async_url)
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
    await engine.dispose()


@pytest.mark.asyncio
async def test_release_entry_blocks_postgres_authority_mismatch(
    pg_async_url: str, require_postgres: None, tmp_path: Path
):
    engine, sessions, report = await _qualified_report(pg_async_url)
    report["artifact"]["authority"]["machine_clue_count"] = 0
    report_path = tmp_path / "clue-qualification.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    observer_engine = create_async_engine(pg_async_url)
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
        or verdict["checks"]["report_signature"] is False
    )

    await observer_engine.dispose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_release_entry_blocks_missing_or_malformed_report(
    pg_async_url: str, require_postgres: None, tmp_path: Path
):
    observer_engine = create_async_engine(pg_async_url)
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


def test_scope_scan_documents_chat_reject_and_source_unavailable():
    result = scope_scan(REPO_ROOT)
    assert result["chat_reject_present"] is True
    assert result["source_unavailable_protocol"] is True
    assert result["forbidden_hits"] == []
    assert result["scope_clean"] is True
