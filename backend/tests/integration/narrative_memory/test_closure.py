"""PostgreSQL cross-dimension closure and one-click analysis tests (Phase 28-04).

REQ-NM-03/04, D-02/D-03/D-04/D-07/D-10: one-click analysis reports each
dimension as available/partial/blocked under a shared CandidateManifest parity
contract, persists durable progress on the build-run row, resumes from the DB
checkpoint after reconnect, emits notification-only SSE frames, and never
writes an active pointer or restores the removed ``/analyze/stream`` endpoint.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
from app.services.narrative_memory.closure import (
    analysis_report_with_progress,
    assemble_sse_frames,
    compute_dimension_closure,
    run_one_click_analysis,
)
from app.services.narrative_memory.contracts import (
    DimensionKind,
    DimensionStatus,
    candidate_manifest_checksum,
    dimension_result_checksum,
)
from app.services.narrative_memory.manifest_contract import (
    ManifestContractError,
    assert_no_pointer_fields,
    manifest_parity_ok,
    validate_candidate_manifest,
)
from app.services.narrative_memory.progress import load_durable_progress
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_arc_worker_pg import _Src
from tests.integration.narrative_memory.test_chapter_state_worker_pg import (
    ControlledTransport,
    _deployment,
    _policy,
    _seed,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def closure_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            user, novel, version, _chapters, _report = await _seed(session)
        yield {
            "factory": factory,
            "owner_id": user.id,
            "novel_id": novel.id,
            "version_id": version.id,
            "version_key": version.version_key,
        }
    finally:
        await engine.dispose()


async def _build_run(env) -> tuple[int, object]:
    worker = NarrativeMemoryBuilderWorker(
        env["factory"],
        inventory_source=_Src(env["factory"]),
        transport=ControlledTransport(),
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=env["owner_id"],
        novel_id=env["novel_id"],
        version_id=env["version_id"],
        run_policy=_policy(),
    )
    result = await worker.process_run(
        owner_id=env["owner_id"],
        novel_id=env["novel_id"],
        version_id=env["version_id"],
    )
    return run_id, result


# ---------------------------------------------------------------------------
# Closure reports every dimension under the CandidateManifest parity contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closure_reports_each_dimension_with_manifest_parity(closure_env) -> None:
    run_id, _result = await _build_run(closure_env)
    async with closure_env["factory"]() as session:
        closure = await compute_dimension_closure(
            session,
            owner_id=closure_env["owner_id"],
            novel_id=closure_env["novel_id"],
            version_id=closure_env["version_id"],
        )
        assert closure.run_id == run_id
        assert closure.manifest_checksum == candidate_manifest_checksum(
            closure.manifest
        )
        assert closure.manifest.checksum == closure.manifest_checksum
        # All five dimensions present with stable per-dimension checksums.
        assert [str(d.dimension) for d in closure.dimensions] == [
            "timeline",
            "relationship",
            "clue",
            "character",
            "world",
        ]
        for result in closure.dimensions:
            assert result.checksum == dimension_result_checksum(result)
            assert result.status in {
                DimensionStatus.AVAILABLE,
                DimensionStatus.PARTIAL,
                DimensionStatus.BLOCKED,
            }
            assert 0.0 <= result.progress <= 1.0
            if result.status == DimensionStatus.BLOCKED:
                assert result.blocked_reason
        # Shared snapshot/cutoff/owner/version/budget/lineage parity holds.
        assert manifest_parity_ok(closure.manifest) is True
        validate_candidate_manifest(closure.manifest)
        # Character content exists in the seeded candidate claims.
        by_kind = {str(d.dimension): d for d in closure.dimensions}
        assert by_kind["character"].status == DimensionStatus.AVAILABLE
        # Facet ranges come from the real candidate hierarchy boundary plan.
        assert closure.facet_ranges
        assert closure.facet_ranges[0].stage_key
        # Report budget is durable ledger-derived.
        assert closure.budget.calls >= 1


@pytest.mark.asyncio
async def test_manifest_rejects_tampered_dimension_in_closure(closure_env) -> None:
    await _build_run(closure_env)
    async with closure_env["factory"]() as session:
        closure = await compute_dimension_closure(
            session,
            owner_id=closure_env["owner_id"],
            novel_id=closure_env["novel_id"],
            version_id=closure_env["version_id"],
        )
        tampered = closure.dimensions[0].model_copy(
            update={"cutoff": closure.cutoff + 7, "checksum": "0" * 64}
        )
        tampered = tampered.model_copy(
            update={"checksum": dimension_result_checksum(tampered)}
        )
        broken = closure.manifest.model_copy(
            update={"dimensions": tuple([tampered, *closure.dimensions[1:]])}
        )
        broken = broken.model_copy(
            update={"checksum": candidate_manifest_checksum(broken)}
        )
        with pytest.raises(ManifestContractError):
            validate_candidate_manifest(broken)


# ---------------------------------------------------------------------------
# One-click analysis persists durable progress; resume is DB-authoritative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_click_persists_durable_progress_and_db_resume(closure_env) -> None:
    await _build_run(closure_env)
    async with closure_env["factory"]() as session:
        report = await analysis_report_with_progress(
            session,
            owner_id=closure_env["owner_id"],
            novel_id=closure_env["novel_id"],
            version_id=closure_env["version_id"],
            persist=True,
        )
        assert report["publication_status"] == "candidate_preview"
        assert len(report["dimensions"]) == 5
        assert report["durable_progress"]["authoritative"] is True
        assert report["sse_frames"]
        await session.commit()

    # Reconnect: a brand-new session reconstructs the same durable state from
    # PostgreSQL rows — never from browser memory.
    async with closure_env["factory"]() as session:
        run = await session.scalar(
            select(NarrativeMemoryBuildRun).where(
                NarrativeMemoryBuildRun.owner_id == closure_env["owner_id"],
                NarrativeMemoryBuildRun.novel_id == closure_env["novel_id"],
                NarrativeMemoryBuildRun.version_id == closure_env["version_id"],
            )
        )
        assert run is not None
        stored = (run.progress or {}).get("dimension_statuses")
        assert stored is not None
        assert set(stored) == {"timeline", "relationship", "clue", "character", "world"}

        progress = await load_durable_progress(
            session,
            owner_id=closure_env["owner_id"],
            novel_id=closure_env["novel_id"],
            version_id=closure_env["version_id"],
        )
        assert progress.run_id == run.id
        assert progress.authoritative is True
        assert progress.dimensions == stored
        assert progress.stage_counts
        # Notification is a digest only; full rows stay in the DB.
        notification = progress.notification
        assert notification["event_type"] == "narrative_memory.progress"
        assert notification["payload"]["authoritative"] is True
        assert notification["payload"]["run_id"] == run.id


@pytest.mark.asyncio
async def test_one_click_is_idempotent_on_resume(closure_env) -> None:
    await _build_run(closure_env)
    async with closure_env["factory"]() as session:
        first = await run_one_click_analysis(
            session,
            owner_id=closure_env["owner_id"],
            novel_id=closure_env["novel_id"],
            version_id=closure_env["version_id"],
        )
        await session.commit()
    async with closure_env["factory"]() as session:
        second = await run_one_click_analysis(
            session,
            owner_id=closure_env["owner_id"],
            novel_id=closure_env["novel_id"],
            version_id=closure_env["version_id"],
        )
        assert second.manifest_checksum == first.manifest_checksum
        assert [
            (str(d.dimension), d.status, d.progress) for d in second.dimensions
        ] == [(str(d.dimension), d.status, d.progress) for d in first.dimensions]


# ---------------------------------------------------------------------------
# Candidate-only: no active pointer writes, no /analyze/stream restoration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closure_and_progress_never_write_pointer(closure_env) -> None:
    await _build_run(closure_env)
    async with closure_env["factory"]() as session:
        await run_one_click_analysis(
            session,
            owner_id=closure_env["owner_id"],
            novel_id=closure_env["novel_id"],
            version_id=closure_env["version_id"],
        )
        await session.commit()
    async with closure_env["factory"]() as session:
        run = await session.scalar(
            select(NarrativeMemoryBuildRun).where(
                NarrativeMemoryBuildRun.owner_id == closure_env["owner_id"],
                NarrativeMemoryBuildRun.novel_id == closure_env["novel_id"],
                NarrativeMemoryBuildRun.version_id == closure_env["version_id"],
            )
        )
        assert run is not None
        assert_no_pointer_fields(dict(run.progress or {}))
        assert_no_pointer_fields(dict((run.progress or {}).get("closure") or {}))
        # The run row itself has no pointer-style status/reason.
        assert run.status not in {"active", "promoted", "current"}


def test_analysis_routes_registered_and_analyze_stream_absent():
    from app.api.narrative_memory import router

    paths = {route.path for route in router.routes}
    assert "/{novel_id}/versions/{version_id}/analysis" in paths
    assert not any("analyze/stream" in path for path in paths)
    # The removed streaming endpoint is never restored (D-10).
    assert not any("/stream" in path for path in paths)


@pytest.mark.asyncio
async def test_analysis_api_smoke(closure_env) -> None:
    """One-click POST/GET through the HTTP layer (owner-scoped, no pointer)."""
    from httpx import ASGITransport, AsyncClient

    from app.core.database import get_db
    from app.core.security import require_agent_actor, require_user
    from app.main import app

    run_id, _result = await _build_run(closure_env)

    async def _override_db():
        async with closure_env["factory"]() as session:
            yield session
            await session.rollback()

    async def _current_user():
        async with closure_env["factory"]() as session:
            user = await session.get(
                __import__("app.models.user", fromlist=["User"]).User,
                closure_env["owner_id"],
            )
            return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_user] = _current_user
    # require_owned_novel resolves auth via require_agent_actor, not require_user.
    app.dependency_overrides[require_agent_actor] = _current_user
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            url = (
                f"/api/narrative-memory/{closure_env['novel_id']}/versions/"
                f"{closure_env['version_id']}/analysis"
            )
            post_resp = await client.post(url)
            assert post_resp.status_code == 200, post_resp.text
            body = post_resp.json()
            assert body["run_id"] == run_id
            assert {d["dimension"] for d in body["dimensions"]} == {
                "timeline",
                "relationship",
                "clue",
                "character",
                "world",
            }
            assert body["durable_progress"]["authoritative"] is True
            assert body["manifest_checksum"]
            assert body["publication_status"] == "candidate_preview"
            assert body["sse_frames"]

            # GET recomputes deterministically without persisting.
            get_resp = await client.get(url)
            assert get_resp.status_code == 200, get_resp.text
            get_body = get_resp.json()
            assert get_body["manifest_checksum"] == body["manifest_checksum"]
            assert get_body["dimensions"] == body["dimensions"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_progress_notification_reuses_agent_sse_envelope(closure_env) -> None:
    await _build_run(closure_env)
    async with closure_env["factory"]() as session:
        closure = await compute_dimension_closure(
            session,
            owner_id=closure_env["owner_id"],
            novel_id=closure_env["novel_id"],
            version_id=closure_env["version_id"],
        )
        frames = assemble_sse_frames(closure.notifications)
        assert len(frames) == 1
        frame = frames[0]
        assert frame.startswith("data: ")
        assert frame.endswith("\n\n")
        assert '"event"' in frame and '"data"' in frame
        assert closure.notifications[0].event_type == "narrative_memory.closure"
        payload = closure.notifications[0].payload
        assert payload["authoritative"] is True
        assert set(payload["dimensions"]) == {
            "timeline",
            "relationship",
            "clue",
            "character",
            "world",
        }
