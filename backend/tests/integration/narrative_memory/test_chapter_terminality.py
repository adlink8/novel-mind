"""Chapter terminal convergence, frozen source manifest, and drift tests.

Phase 28-02 (REQ-NM-01/05, D-02/D-03/D-04/D-05/D-08): every chapter reaches a
durable terminal state (completed/isolated/blocked) with a stable reason; the
frozen source manifest is DB-recomputable and source drift fails closed; a
partial chapter failure never triggers an unconditional whole-book restart;
and ChapterAnalysisArtifact carries bounded context/continuity fields whose
digests are never retrieval-index inputs or EvidenceRef authority.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import undefer

from app.models.narrative_memory import (
    NarrativeMemorySourceLink,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.builder_contracts import (
    ChapterAnalysisArtifact,
    NEXT_HINT_BLOCKED_REASON,
    ReasonCode,
    TerminalState,
    assert_digests_never_evidence_refs,
    build_chapter_analysis_artifact,
    hint_safe_at_cutoff,
)
from app.services.narrative_memory.builder_gateway import GatewayError
from app.services.narrative_memory.builder_repository import BuilderRepository
from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
from app.services.narrative_memory.contracts import CandidateVersionSpec, ModelLineage
from app.services.narrative_memory.recovery import build_resume_plan
from app.services.narrative_memory.source_manifest import (
    SourceManifest,
    detect_chapter_drift,
    frozen_manifest_from_progress,
    recompute_source_manifest,
    source_manifest_drift_reasons,
    store_frozen_manifest,
)
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_arc_worker_pg import _Src
from tests.integration.narrative_memory.test_chapter_state_worker_pg import (
    ControlledTransport,
    _deployment,
    _policy,
)

pytestmark = pytest.mark.integration

HEX_A = "a" * 64
LONG_BOOK_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "narrative_memory"
    / "long_book_v1.json"
)


def _load_long_book() -> dict:
    return json.loads(LONG_BOOK_FIXTURE.read_text(encoding="utf-8"))


class ProviderFailureTransport(ControlledTransport):
    """Raises a GatewayError for configured chapters (provider 5xx analog)."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_chapters: set[int] = set()

    async def complete(self, **kwargs):
        chapter_number = kwargs["payload"]["chapter_number"]
        if chapter_number in self.fail_chapters:
            raise GatewayError("injected_provider_failure")
        return await super().complete(**kwargs)


async def _seed_long_book(session: AsyncSession):
    fixture = _load_long_book()
    user = User(
        username="terminal-owner",
        email="terminal-owner@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    novel = Novel(owner_id=user.id, title="Long Book", status="ready")
    session.add(novel)
    await session.flush()
    chapters = [
        Chapter(
            novel_id=novel.id,
            chapter_number=int(item["number"]),
            title=item["title"],
            content=item["content"],
            word_count=len(item["content"]),
        )
        for item in fixture["chapters"]
    ]
    session.add_all(chapters)
    await session.flush()
    await create_and_persist_hierarchy_build(
        session,
        novel_id=novel.id,
        chapters=[
            {
                "chapter_id": chapter.id,
                "chapter_number": chapter.chapter_number,
                "content": chapter.content,
            }
            for chapter in chapters
        ],
        promote_active=True,
        force_full=True,
    )
    await session.flush()
    report = await audit_assets(
        PostgresAuditSource(session), owner_id=user.id, novel_id=novel.id
    )
    assert report.provider_calls_allowed is True
    authority = CandidateAuthority(session)
    version = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=CandidateVersionSpec(
            version_key="long-book-v1",
            prompt_hash=HEX_A,
            schema_hash=HEX_A,
            model_lineage=ModelLineage(
                provider="test", model="m", deployment="fixed", revision="1"
            ),
            decoding_hash=HEX_A,
            config_hash=HEX_A,
            policy_hash=HEX_A,
        ),
        eligibility_report=report,
    )
    await session.commit()
    return user, novel, version, chapters, report


@pytest.fixture
async def builder_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            user, novel, version, chapters, _report = await _seed_long_book(session)
        yield {
            "factory": factory,
            "owner_id": user.id,
            "novel_id": novel.id,
            "version_id": version.id,
            "chapters": chapters,
        }
    finally:
        await engine.dispose()


async def _chapter_stages(factory, run_id: int) -> list[NarrativeMemoryBuildStage]:
    async with factory() as session:
        rows = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                )
            )
        ).all()
        return list(rows)


def _chapter_calls(transport: ControlledTransport) -> int:
    return sum(1 for c in transport.calls if "chapter_number" in c["payload"])


# ---------------------------------------------------------------------------
# Long-book terminal coverage: every chapter converges, no silent pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_book_every_chapter_terminal_with_reason(builder_env) -> None:
    fixture = _load_long_book()
    transport = ProviderFailureTransport()
    transport.fail_chapters = set(fixture["isolated_chapters"])
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    result = await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    stages = await _chapter_stages(builder_env["factory"], run_id)
    assert len(stages) == fixture["chapter_count"]
    by_number = {
        int(s.chapter_start): s for s in stages
    }

    # Every chapter reached a durable terminal state with a stable reason.
    for stage in stages:
        assert stage.terminal_state in {
            TerminalState.COMPLETED.value,
            TerminalState.ISOLATED.value,
            TerminalState.BLOCKED.value,
        }, f"{stage.stage_key} has no terminal state"
        assert stage.reason_code, f"{stage.stage_key} lacks a reason code"

    for number in fixture["expected"]["isolated"]:
        stage = by_number[number]
        assert stage.status == "failed"
        assert stage.terminal_state == TerminalState.ISOLATED.value
        assert stage.reason_code == fixture["isolated_reason_code"]

    for number in fixture["expected"]["completed"]:
        stage = by_number[number]
        assert stage.status == "completed"
        assert stage.terminal_state == TerminalState.COMPLETED.value
        assert stage.reason_code == ReasonCode.COMPLETED_CANDIDATE.value

    # Partial failure → partial run, never whole-book restart or fake completion.
    assert result.status == "partial"
    assert result.failed_stages
    assert result.source_manifest_checksum

    # Resume plan finds no silent pending chapter stages.
    async with builder_env["factory"]() as session:
        repo = BuilderRepository(session)
        stages = await repo.list_stages(run_id)
        plan = build_resume_plan(stages)
    assert plan.has_silent_pending is False
    assert set(plan.terminal)  # every stage is terminal


# ---------------------------------------------------------------------------
# Frozen source manifest: DB-recomputed checksums match; drift is detected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frozen_source_manifest_is_db_recomputable(builder_env) -> None:
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=ControlledTransport(),
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    async with builder_env["factory"]() as session:
        run = await session.get(NarrativeMemoryBuildRun, run_id)
        assert run is not None
        frozen = frozen_manifest_from_progress(run.progress)
        assert frozen is not None
        assert frozen.manifest_checksum == (run.progress or {}).get(
            "source_manifest_checksum"
        )
        # Independent recomputation from current authority rows is identical.
        recomputed = await recompute_source_manifest(
            session,
            version=await session.get(
                NarrativeMemoryVersion, builder_env["version_id"]
            ),
        )
    assert recomputed.manifest_checksum == frozen.manifest_checksum
    assert source_manifest_drift_reasons(frozen, recomputed) == []
    assert detect_chapter_drift(frozen, recomputed) == {}


# ---------------------------------------------------------------------------
# Source drift fails closed: drifted chapters block, siblings proceed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_mutation_drift_fails_closed_on_resume(builder_env) -> None:
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=ControlledTransport(),
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    # Process only the first two chapters so chapters 3..12 stay pending.
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        max_stages=2,
    )
    async with builder_env["factory"]() as session:
        stages = await _chapter_stages(builder_env["factory"], run_id)
        assert {int(s.chapter_start) for s in stages if s.status == "completed"} == {
            1,
            2,
        }
        artifacts_before = {
            int(s.chapter_start): s.artifact_checksum
            for s in stages
            if s.status == "completed"
        }
        run = await session.get(NarrativeMemoryBuildRun, run_id)
        assert run is not None
        frozen = frozen_manifest_from_progress(run.progress)
        assert frozen is not None
        # The frozen source snapshot now disagrees with current authority rows:
        # chapter 5's stored digest no longer matches its live content hash.
        payload = frozen.as_dict()
        for item in payload["chapters"]:
            if item["chapter_number"] == 5:
                item["content_hash"] = "f" * 64
        tampered = SourceManifest.from_dict(payload)
        assert tampered.manifest_checksum == frozen.manifest_checksum
        run.progress = store_frozen_manifest(dict(run.progress), tampered)
        await session.commit()

        # Recomputation from the live DB exposes the drift (D-05).
        version = await session.get(
            NarrativeMemoryVersion, builder_env["version_id"]
        )
        recomputed = await recompute_source_manifest(session, version=version)
        assert source_manifest_drift_reasons(tampered, recomputed)
        assert "chapter:5:chapter_content_drift" in source_manifest_drift_reasons(
            tampered, recomputed
        )

    # Resume the full book. Chapter 5 must be blocked, never re-run on stale input.
    result = await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    assert result.status == "partial"
    assert "chapter_state:5" in result.blocked_stages

    async with builder_env["factory"]() as session:
        stages = await _chapter_stages(builder_env["factory"], run_id)
        by_number = {int(s.chapter_start): s for s in stages}
        blocked = by_number[5]
        assert blocked.status == "blocked_dependency"
        assert blocked.terminal_state == TerminalState.BLOCKED.value
        assert blocked.reason_code == ReasonCode.SOURCE_DRIFT.value

        # Completed siblings are never rewound or restarted.
        for number, checksum in artifacts_before.items():
            assert by_number[number].artifact_checksum == checksum
        for number in (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12):
            assert by_number[number].status == "completed", number
        # The drifted chapter was not executed against stale evidence.
        assert blocked.attempt_count == 0


# ---------------------------------------------------------------------------
# Content mutation is also caught by the strict eligibility gate (V5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_mutation_fails_closed_at_eligibility_gate(builder_env) -> None:
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=ControlledTransport(),
        deployment=_deployment(),
    )
    await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        max_stages=1,
    )
    # Mutate a pending chapter's source content after the freeze.
    async with builder_env["factory"]() as session:
        chapter3 = await session.scalar(
            select(Chapter)
            .where(
                Chapter.novel_id == builder_env["novel_id"],
                Chapter.chapter_number == 3,
            )
            .options(undefer(Chapter.content))
        )
        assert chapter3 is not None
        chapter3.content = chapter3.content + "漂移的正文。"
        await session.commit()

    # The strict eligibility gate rejects the resume (fail closed, V5).
    from app.services.narrative_memory.builder_repository import (
        BuilderRepositoryError,
    )

    with pytest.raises(BuilderRepositoryError):
        await worker.process_run(
            owner_id=builder_env["owner_id"],
            novel_id=builder_env["novel_id"],
            version_id=builder_env["version_id"],
        )


# ---------------------------------------------------------------------------
# Partial failure + requeue resumes only the isolated chapter (no restart-all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requeue_resumes_only_isolated_chapter(builder_env) -> None:
    transport = ControlledTransport()
    transport.fail_chapters = {5}
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    async with builder_env["factory"]() as session:
        stages = await _chapter_stages(builder_env["factory"], run_id)
        by_number = {int(s.chapter_start): s for s in stages}
        assert by_number[5].status == "failed"
        assert by_number[5].terminal_state == TerminalState.ISOLATED.value
        assert by_number[1].status == "completed"
        assert by_number[12].status == "completed"
        # Requeue the isolated chapter as an operator would.
        repo = BuilderRepository(session)
        await repo.mark_stage(by_number[5], status="pending")
        await session.commit()
    chapter_calls_after_run1 = _chapter_calls(transport)

    # Clear the failure injection and resume.
    transport.fail_chapters = set()
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    async with builder_env["factory"]() as session:
        stages = await _chapter_stages(builder_env["factory"], run_id)
        by_number = {int(s.chapter_start): s for s in stages}
        assert by_number[5].status == "completed"
        assert by_number[5].terminal_state == TerminalState.COMPLETED.value
        # Sibling artifacts are byte-identical (no whole-book restart).
        for number in (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12):
            assert by_number[number].status == "completed"
    # Only the requeued chapter re-ran on resume.
    assert _chapter_calls(transport) == chapter_calls_after_run1 + 1


# ---------------------------------------------------------------------------
# ChapterAnalysisArtifact: bounded context/continuity, non-authority digests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chapter_artifact_bounded_context_and_continuity(builder_env) -> None:
    fixture = _load_long_book()
    transport = ProviderFailureTransport()
    transport.fail_chapters = set(fixture["isolated_chapters"])
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    artifacts: list[tuple[int, ChapterAnalysisArtifact, NarrativeMemoryBuildStage]] = []
    async with builder_env["factory"]() as session:
        stages = await _chapter_stages(builder_env["factory"], run_id)
        version = await session.get(
            NarrativeMemoryVersion, builder_env["version_id"]
        )
        for stage in stages:
            if stage.status != "completed":
                continue
            payload = (stage.checkpoint or {}).get("chapter_analysis_artifact")
            assert payload, f"{stage.stage_key} missing chapter artifact"
            artifact = ChapterAnalysisArtifact.model_validate(payload)
            chapter_number = int(stage.chapter_start)
            assert artifact.chapter_id == stage.checkpoint["chapter_id"]
            assert artifact.cutoff == chapter_number
            # Source/input hash binding (D-08).
            assert artifact.source_snapshot_hash == version.source_snapshot_hash
            assert artifact.input_hash == stage.package_checksum
            assert artifact.chapter_digest == stage.checkpoint["chapter_digest"]
            assert artifact.spoiler_policy_version == "spoiler-policy.v1"
            # The bounded context payloads respect their caps.
            if artifact.previous_context_summary is not None:
                assert len(artifact.previous_context_summary) <= 2000
            if artifact.continuity_notes is not None:
                assert len(artifact.continuity_notes) <= 1200
            # Next hint never leaks future facts beyond the chapter cutoff.
            if artifact.next_context_hint is not None:
                assert hint_safe_at_cutoff(
                    artifact.next_context_hint, cutoff=chapter_number
                )
            artifacts.append((chapter_number, artifact, stage))

    assert len(artifacts) == len(fixture["expected"]["completed"])

    # Digests are compressed payloads only: never EvidenceRefs, never indexed.
    async with builder_env["factory"]() as session:
        authority_hashes = (
            await session.scalars(
                select(NarrativeMemorySourceLink.content_hash).where(
                    NarrativeMemorySourceLink.version_id
                    == builder_env["version_id"]
                )
            )
        ).all()
        indexed_contents = (
            await session.scalars(
                select(TextChunk.content).where(
                    TextChunk.novel_id == builder_env["novel_id"]
                )
            )
        ).all()
    for _number, artifact, _stage in artifacts:
        digests = [artifact.chapter_digest, *artifact.chunk_digests]
        # Static role guard.
        assert_digests_never_evidence_refs(
            digests,
            authority_content_hashes=list(authority_hashes),
            retrieval_index_inputs=list(indexed_contents),
        )
        # DB-level: no digest is stored as an EvidenceRef content hash and none
        # is a retrieval-index chunk payload.
        assert not any(d in authority_hashes for d in digests)
        assert not any(d in indexed_contents for d in digests)


# ---------------------------------------------------------------------------
# Next-context hint spoiler guard
# ---------------------------------------------------------------------------


def test_next_hint_spoiler_guard_blocks_future_facts() -> None:
    safe = build_chapter_analysis_artifact(
        chapter_id=1,
        chapter_number=2,
        source_snapshot_hash=HEX_A,
        input_hash=HEX_A,
        spoiler_policy_version="spoiler-policy.v1",
        max_length=2000,
        context_payload={},
        chunk_reprs=[],
        next_context_hint="Disambiguate between chapter 1 and chapter 2 events.",
    )
    assert safe.next_context_hint is not None
    assert safe.next_hint_reason_code is None

    unsafe = build_chapter_analysis_artifact(
        chapter_id=1,
        chapter_number=2,
        source_snapshot_hash=HEX_A,
        input_hash=HEX_A,
        spoiler_policy_version="spoiler-policy.v1",
        max_length=2000,
        context_payload={},
        chunk_reprs=[],
        next_context_hint="Chapter 9 will reveal the betrayal.",
    )
    # Future facts are omitted and a stable reason code is recorded.
    assert unsafe.next_context_hint is None
    assert unsafe.next_hint_reason_code == NEXT_HINT_BLOCKED_REASON
    assert not hint_safe_at_cutoff("chapter 9 reveal", cutoff=2)
    assert hint_safe_at_cutoff("chapter 2 only", cutoff=2)


# ---------------------------------------------------------------------------
# Manifest recompute rejects silent content mutation even without a resume
# ---------------------------------------------------------------------------


def test_detect_chapter_drift_is_byte_identical_for_unchanged_source() -> None:
    fixture = _load_long_book()
    assert len(fixture["chapters"]) == fixture["chapter_count"]
    numbers = [item["number"] for item in fixture["chapters"]]
    assert numbers == list(range(1, fixture["chapter_count"] + 1))
    assert set(numbers) == set(fixture["expected"]["completed"]) | set(
        fixture["expected"]["isolated"]
    ) | set(fixture["expected"]["blocked"])
