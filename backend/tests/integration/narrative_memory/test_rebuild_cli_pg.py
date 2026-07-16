"""Real CLI subprocess tests for Phase 16 rebuild commands."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    CandidateVersionSpec,
    ModelLineage,
)
from app.services.narrative_memory.manifests import seal_and_report
from app.services.narrative_memory.rebuild_contracts import stable_checksum
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX = "a" * 64
BACKEND = Path(__file__).resolve().parents[3]
CLI = BACKEND / "scripts" / "run_narrative_memory_rebuild.py"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)


def _spec(version_key: str, *, parent_version_id: int | None = None) -> CandidateVersionSpec:
    return CandidateVersionSpec(
        version_key=version_key,
        prompt_hash=HEX,
        schema_hash=HEX,
        model_lineage=ModelLineage(
            provider="test", model="m", deployment="fixed", revision="1"
        ),
        decoding_hash=HEX,
        config_hash=HEX,
        policy_hash=HEX,
        parent_version_id=parent_version_id,
    )


async def _package_for(
    session: AsyncSession,
    version: NarrativeMemoryVersion,
    chapters: list[Chapter],
) -> CandidatePackage:
    evidence_rows = list(
        (
            await session.scalars(
                select(ChunkHierarchyNode)
                .where(
                    ChunkHierarchyNode.build_id == version.hierarchy_build_id,
                    ChunkHierarchyNode.level == "evidence",
                )
                .order_by(
                    ChunkHierarchyNode.chapter_number,
                    ChunkHierarchyNode.order_index,
                    ChunkHierarchyNode.id,
                )
            )
        ).all()
    )
    by_chapter = {}
    for row in evidence_rows:
        by_chapter.setdefault(row.chapter_number, row)
    nodes = [
        {
            "node_kind": "global_story",
            "node_key": "global_story:book",
            "chapter_start": 1,
            "chapter_end": len(chapters),
            "schema_version": "memory-node.v1",
        },
        {
            "node_kind": "story_arc",
            "node_key": "story_arc:1-3",
            "chapter_start": 1,
            "chapter_end": len(chapters),
            "schema_version": "memory-node.v1",
        },
    ]
    claims = []
    edges = [
        {
            "edge_type": "contains",
            "source_node_key": "global_story:book",
            "target_node_key": "story_arc:1-3",
        }
    ]
    source_links = []
    for ch in chapters:
        leaf = by_chapter[ch.chapter_number]
        node_key = f"chapter_state:{ch.id}"
        claim_key = f"claim:ch:{ch.chapter_number}"
        source_key = f"source:ch:{ch.chapter_number}"
        nodes.append(
            {
                "node_kind": "chapter_state",
                "node_key": node_key,
                "chapter_start": ch.chapter_number,
                "chapter_end": ch.chapter_number,
                "schema_version": "memory-node.v1",
            }
        )
        claims.append(
            {
                "claim_key": claim_key,
                "node_key": node_key,
                "payload": {
                    "claim_kind": "entity_state",
                    "entity_kind": "character",
                    "entity_key": "character:lin",
                    "dimension": "location",
                    "prior": {"value_kind": "unknown"},
                    "current": {
                        "value_kind": "text",
                        "value": f"place-{ch.chapter_number}",
                    },
                    "change": "establish",
                },
                "uncertainty": "certain",
                "confidence": 0.95,
                "visible_from_chapter": ch.chapter_number,
                "source_keys": [source_key],
            }
        )
        edges.append(
            {
                "edge_type": "contains",
                "source_node_key": "story_arc:1-3",
                "target_node_key": node_key,
            }
        )
        source_links.append(
            {
                "source_key": source_key,
                "claim_key": claim_key,
                "source_kind": "hierarchy",
                "hierarchy_build_id": version.hierarchy_build_id,
                "evidence_node_id": leaf.node_id,
                "chapter_id": leaf.chapter_id,
                "chapter_number": leaf.chapter_number,
                "source_start": leaf.source_start,
                "source_end": leaf.source_end,
                "content_hash": leaf.content_hash,
                "source_snapshot_hash": version.source_snapshot_hash,
            }
        )
    leaf1 = by_chapter[1]
    for claim_key, node_key, payload in (
        (
            "claim:global",
            "global_story:book",
            {
                "claim_kind": "world_state_delta",
                "subject_key": "world:capital",
                "dimension": "political_order",
                "prior": {"value_kind": "unknown"},
                "current": {"value_kind": "text", "value": "stable"},
                "change": "establish",
            },
        ),
        (
            "claim:arc",
            "story_arc:1-3",
            {
                "claim_kind": "event_fact",
                "event_kind": "discovery",
                "actor_keys": ["character:lin"],
                "object_keys": [],
                "chapter_start": 1,
                "chapter_end": len(chapters),
                "outcome": {"value_kind": "text", "value": "arc-summary"},
            },
        ),
    ):
        claims.append(
            {
                "claim_key": claim_key,
                "node_key": node_key,
                "payload": payload,
                "uncertainty": "certain",
                "confidence": 0.7,
                "visible_from_chapter": 1,
                "source_keys": [f"source:{claim_key}"],
            }
        )
        source_links.append(
            {
                "source_key": f"source:{claim_key}",
                "claim_key": claim_key,
                "source_kind": "hierarchy",
                "hierarchy_build_id": version.hierarchy_build_id,
                "evidence_node_id": leaf1.node_id,
                "chapter_id": leaf1.chapter_id,
                "chapter_number": leaf1.chapter_number,
                "source_start": leaf1.source_start,
                "source_end": leaf1.source_end,
                "content_hash": leaf1.content_hash,
                "source_snapshot_hash": version.source_snapshot_hash,
            }
        )
    return CandidatePackage.model_validate_json(
        json.dumps(
            {
                "nodes": nodes,
                "claims": claims,
                "edges": edges,
                "source_links": source_links,
            },
            ensure_ascii=False,
        )
    )


async def _seed(session: AsyncSession, *, edit_chapter: int | None = None) -> dict:
    user = User(
        username="cli-rebuild-owner",
        email="cli-rebuild@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    novel = Novel(owner_id=user.id, title="CLI Rebuild", status="ready")
    session.add(novel)
    await session.flush()
    contents = {1: "甲乙丙丁戊己", 2: "庚辛壬癸子丑", 3: "寅卯辰巳午未"}
    chapters = [
        Chapter(
            novel_id=novel.id,
            chapter_number=n,
            title=f"Chapter {n}",
            content=contents[n],
            word_count=len(contents[n]),
        )
        for n in (1, 2, 3)
    ]
    session.add_all(chapters)
    await session.flush()
    await create_and_persist_hierarchy_build(
        session,
        novel_id=novel.id,
        chapters=[
            {
                "chapter_id": c.id,
                "chapter_number": c.chapter_number,
                "content": c.content,
            }
            for c in chapters
        ],
        promote_active=True,
        force_full=True,
    )
    await session.flush()
    report = await audit_assets(
        PostgresAuditSource(session), owner_id=user.id, novel_id=novel.id
    )
    authority = CandidateAuthority(session)
    parent = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=_spec("parent-v1"),
        eligibility_report=report,
    )
    await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=parent.id,
        package=await _package_for(session, parent, chapters),
    )
    boundary = {
        "source_kind": "explicit_volume",
        "chapter_min": 1,
        "chapter_max": 3,
        "chapter_to_parent": {
            str(ch.chapter_number): "story_arc:1-3" for ch in chapters
        },
        "parent_to_global": {"story_arc:1-3": "global_story:book"},
    }
    session.add(
        NarrativeMemoryBuildRun(
            owner_id=user.id,
            novel_id=novel.id,
            version_id=parent.id,
            eligibility_report_checksum=parent.eligibility_report_checksum,
            eligibility_policy_version=parent.eligibility_policy_version,
            status="completed",
            progress={},
            run_policy={},
            boundary_plan=boundary,
            boundary_plan_checksum=stable_checksum(boundary),
        )
    )
    await session.flush()
    await seal_and_report(
        session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=parent.id,
    )
    if edit_chapter is not None:
        ch = next(c for c in chapters if c.chapter_number == edit_chapter)
        ch.content = ch.content + "改"
        ch.word_count = len(ch.content)
        await session.flush()
        await create_and_persist_hierarchy_build(
            session,
            novel_id=novel.id,
            chapters=[
                {
                    "chapter_id": c.id,
                    "chapter_number": c.chapter_number,
                    "content": c.content,
                }
                for c in chapters
            ],
            promote_active=True,
            force_full=True,
        )
        await session.flush()
        report2 = await audit_assets(
            PostgresAuditSource(session), owner_id=user.id, novel_id=novel.id
        )
        target = await authority.create_version(
            owner_id=user.id,
            novel_id=novel.id,
            spec=_spec("target-v1", parent_version_id=parent.id),
            eligibility_report=report2,
        )
    else:
        target = await authority.create_version(
            owner_id=user.id,
            novel_id=novel.id,
            spec=_spec("target-v1", parent_version_id=parent.id),
            eligibility_report=report,
        )
    await session.commit()
    return {
        "owner_id": user.id,
        "novel_id": novel.id,
        "parent_version_id": parent.id,
        "target_version_id": target.id,
        "hierarchy_build_id": target.hierarchy_build_id,
        "eligibility_checksum": target.eligibility_report_checksum,
    }


def _cli_env(pg_async_url: str) -> dict[str, str]:
    import os

    env = os.environ.copy()
    env["NOVELMIND_DATABASE_URL"] = pg_async_url
    # Keep debug true so production secret validators do not block subprocess CLI.
    env["NOVELMIND_DEBUG"] = "true"
    env.setdefault(
        "NOVELMIND_SECRET_KEY",
        "ci-only-integration-secret-key-32chars-min",
    )
    env.setdefault(
        "NOVELMIND_ENCRYPTION_KEY",
        "ci-only-integration-encryption-key-32c",
    )
    return env


def _run_cli(args: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [str(PYTHON), str(CLI), *args]
    return subprocess.run(
        cmd,
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _parse_json_stdout(stdout: str) -> dict:
    """Extract CLI JSON object (ignore SQLAlchemy echo noise)."""
    text = (stdout or "").strip()
    if not text:
        raise ValueError("empty CLI stdout")
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and "command" in payload:
            return payload
    except json.JSONDecodeError:
        pass
    # Prefer objects that include the CLI "command" field.
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for i in reversed(starts):
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[i : j + 1]
                    if '"command"' not in chunk:
                        break
                    try:
                        payload = json.loads(chunk)
                    except json.JSONDecodeError:
                        break
                    if isinstance(payload, dict) and "command" in payload:
                        return payload
                    break
    raise ValueError(f"could not parse CLI JSON from stdout:\n{text[:800]}")


@pytest.fixture
async def cli_env(empty_postgres: str, pg_async_url: str, monkeypatch):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    # Settings use env_prefix NOVELMIND_
    monkeypatch.setenv("NOVELMIND_DATABASE_URL", pg_async_url)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory, pg_async_url
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cli_plan_status_execute_report_no_change(cli_env) -> None:
    factory, pg_async_url = cli_env
    async with factory() as session:
        ctx = await _seed(session, edit_chapter=None)

    env = _cli_env(pg_async_url)

    plan_proc = _run_cli(
        [
            "plan",
            "--owner-id",
            str(ctx["owner_id"]),
            "--novel-id",
            str(ctx["novel_id"]),
            "--parent-version-id",
            str(ctx["parent_version_id"]),
            "--target-version-id",
            str(ctx["target_version_id"]),
            "--hierarchy-build-id",
            ctx["hierarchy_build_id"],
            "--eligibility-checksum",
            ctx["eligibility_checksum"],
        ],
        env=env,
    )
    assert plan_proc.returncode == 0, plan_proc.stderr + plan_proc.stdout
    plan_out = _parse_json_stdout(plan_proc.stdout)
    assert plan_out["command"] == "plan"
    assert plan_out["provider_calls"] == 0
    assert plan_out["plan_id"]

    status_proc = _run_cli(
        [
            "status",
            "--owner-id",
            str(ctx["owner_id"]),
            "--novel-id",
            str(ctx["novel_id"]),
            "--parent-version-id",
            str(ctx["parent_version_id"]),
            "--target-version-id",
            str(ctx["target_version_id"]),
        ],
        env=env,
    )
    assert status_proc.returncode == 0, status_proc.stderr
    status = _parse_json_stdout(status_proc.stdout)
    assert status["decisions"]["carried"] >= 1

    exec_proc = _run_cli(
        [
            "execute",
            "--owner-id",
            str(ctx["owner_id"]),
            "--novel-id",
            str(ctx["novel_id"]),
            "--parent-version-id",
            str(ctx["parent_version_id"]),
            "--target-version-id",
            str(ctx["target_version_id"]),
        ],
        env=env,
    )
    assert exec_proc.returncode == 0, exec_proc.stderr + exec_proc.stdout
    exec_out = _parse_json_stdout(exec_proc.stdout)
    assert exec_out["provider_calls_in_oracle_or_carry"] == 0
    assert exec_out["carried_nodes"]

    report_proc = _run_cli(
        [
            "report",
            "--owner-id",
            str(ctx["owner_id"]),
            "--novel-id",
            str(ctx["novel_id"]),
            "--parent-version-id",
            str(ctx["parent_version_id"]),
            "--target-version-id",
            str(ctx["target_version_id"]),
            "--envelope-input",
            "100",
            "--envelope-output",
            "50",
            "--price-input",
            "1.0",
            "--price-output",
            "2.0",
        ],
        env=env,
    )
    assert report_proc.returncode == 0, report_proc.stderr + report_proc.stdout
    report = _parse_json_stdout(report_proc.stdout)
    assert report["observed_actual"]["calls"] == 0
    assert report["carry_reuse"]["carried_item_count"] >= 1
    assert report["avoided_upper_bound"]["calls"] >= 0

    # Parent pointer / attempts remain clean
    async with factory() as session:
        attempts = await session.scalar(
            text(
                "SELECT count(*) FROM narrative_memory_build_model_call_attempts a "
                "JOIN narrative_memory_build_runs r ON r.id = a.run_id "
                "WHERE r.version_id = :v"
            ),
            {"v": ctx["target_version_id"]},
        )
        assert int(attempts or 0) == 0


@pytest.mark.asyncio
async def test_cli_rejects_forbidden_options() -> None:
    proc = _run_cli(
        [
            "plan",
            "--owner-id",
            "1",
            "--novel-id",
            "1",
            "--parent-version-id",
            "1",
            "--target-version-id",
            "2",
            "--hierarchy-build-id",
            "x",
            "--eligibility-checksum",
            "y",
            "--promote",
        ]
    )
    assert proc.returncode == 3
    assert "forbidden" in (proc.stderr + proc.stdout).lower()


@pytest.mark.asyncio
async def test_cli_edit_fixture_dirty_stages(cli_env) -> None:
    factory, pg_async_url = cli_env
    env = _cli_env(pg_async_url)
    async with factory() as session:
        ctx = await _seed(session, edit_chapter=2)

    plan_proc = _run_cli(
        [
            "plan",
            "--owner-id",
            str(ctx["owner_id"]),
            "--novel-id",
            str(ctx["novel_id"]),
            "--parent-version-id",
            str(ctx["parent_version_id"]),
            "--target-version-id",
            str(ctx["target_version_id"]),
            "--hierarchy-build-id",
            ctx["hierarchy_build_id"],
            "--eligibility-checksum",
            ctx["eligibility_checksum"],
        ],
        env=env,
    )
    assert plan_proc.returncode == 0, plan_proc.stderr
    plan_out = _parse_json_stdout(plan_proc.stdout)
    assert plan_out["command"] == "plan"

    exec_proc = _run_cli(
        [
            "execute",
            "--owner-id",
            str(ctx["owner_id"]),
            "--novel-id",
            str(ctx["novel_id"]),
            "--parent-version-id",
            str(ctx["parent_version_id"]),
            "--target-version-id",
            str(ctx["target_version_id"]),
        ],
        env=env,
    )
    assert exec_proc.returncode == 0, exec_proc.stderr + exec_proc.stdout
    exec_out = _parse_json_stdout(exec_proc.stdout)
    assert exec_out["dirty_stage_keys"]  # at least one dirty stage

    cancel_proc = _run_cli(
        [
            "cancel",
            "--owner-id",
            str(ctx["owner_id"]),
            "--novel-id",
            str(ctx["novel_id"]),
            "--parent-version-id",
            str(ctx["parent_version_id"]),
            "--target-version-id",
            str(ctx["target_version_id"]),
        ],
        env=env,
    )
    assert cancel_proc.returncode == 0, cancel_proc.stderr
    assert _parse_json_stdout(cancel_proc.stdout)["cancel_requested"] is True
