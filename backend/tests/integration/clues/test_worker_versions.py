"""Durable clue worker: restart, budget, CAS promotion/rollback."""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import clues as clues_api
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueModelCallAttempt,
    MachineClue,
)
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.clues.versions import (
    ManifestValidationError,
    StalePointerError,
    promote_version,
    rollback_version,
    snapshot_manifest,
)
from app.services.clues.worker import (
    ClueModelDeployment,
    ClueWorkerRuntime,
    run_clue_worker,
)
from app.services.clues.budget import ClueCallRepository, BudgetPolicy

pytestmark = pytest.mark.integration

HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _seed_hierarchy(db_session, *, title: str = "Clue worker novel"):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    if owner is None:
        owner = User(username="testuser", email="test@example.com", hashed_password="x")
        db_session.add(owner)
        await db_session.flush()
    novel = Novel(owner_id=owner.id, title=title, status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapters = [
        Chapter(novel_id=novel.id, chapter_number=1, title="One", content="A sealed letter appears."),
        Chapter(novel_id=novel.id, chapter_number=3, title="Three", content="The letter is opened at last."),
    ]
    db_session.add_all(chapters)
    await db_session.flush()
    build = ChunkBuild(
        build_id=f"clue-build-{novel.id}",
        novel_id=novel.id,
        status="active",
        source_snapshot_hash=HEX_A,
        manifest_checksum=HEX_B,
        chunker_name="test",
        chunker_version="1",
        chunker_config_hash=HEX_C,
        collection_name="test",
        is_candidate=False,
        immutable=True,
    )
    db_session.add(build)
    db_session.add(
        ChunkActivePointer(
            novel_id=novel.id,
            build_id=build.build_id,
            committed_at=__import__("datetime").datetime.now(
                __import__("datetime").UTC
            ),
        )
    )
    for index, chapter in enumerate(chapters):
        text = chapter.content
        db_session.add(
            ChunkHierarchyNode(
                build_id=build.build_id,
                novel_id=novel.id,
                node_id=f"evidence-{chapter.id}",
                level="evidence",
                chapter_id=chapter.id,
                chapter_number=chapter.chapter_number,
                parent_id=f"scene-{chapter.id}",
                child_ids=[],
                content=text,
                content_hash=_hash(text),
                source_start=0,
                source_end=len(text),
                chunk_type="paragraph",
                decision_lineage=[],
                order_index=index,
            )
        )
    await db_session.commit()
    return owner, novel, chapters


def _deployment(*, unknown_price: bool = False) -> ClueModelDeployment:
    return ClueModelDeployment(
        provider="test",
        model_id="clue-judge",
        revision="r1",
        input_price_per_million=None if unknown_price else Decimal("1"),
        output_price_per_million=None if unknown_price else Decimal("2"),
    )


def _judgment_for_candidate(candidate_id: str, cue_id: str, later_id: str | None = None) -> dict:
    payload = {
        "schema_version": "clue-semantic-judgment.v1",
        "candidate_id": candidate_id,
        "classification": "cue_only",
        "cue_evidence_ids": [cue_id],
        "later_evidence_ids": [later_id] if later_id else [],
        "confidence": 0.92,
        "conflict_flags": [],
        "rationale": "Validated cue appears early in the narrative.",
    }
    return payload


async def _runtime(db_session, *, deployment=None, outputs=None, max_calls=100):
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    return ClueWorkerRuntime(
        sessions=sessions,
        call_repo=ClueCallRepository(sessions),
        deployment=deployment or _deployment(),
        budget_policy=BudgetPolicy(
            max_calls=max_calls,
            max_input_tokens=1_000_000,
            max_output_tokens=100_000,
            max_cost_usd=Decimal("10"),
        ),
        deterministic_outputs=outputs or {},
        deterministic_as_cache=True,
    )


@pytest.mark.asyncio
async def test_worker_promotes_version_and_restart_is_idempotent(
    db_session, auth_client, monkeypatch
):
    owner, novel, chapters = await _seed_hierarchy(db_session)
    # Pre-build a single deterministic candidate id by running once with empty
    # outputs first is hard; instead inject after discovering package from a dry path.
    # Use worker with empty det outputs → judge would need network; so seed det via
    # package-aware approach: monkeypatch recall to one draft.

    from app.services.clues.candidates import ClueCandidateDraft
    from app.services.clues.evidence import make_clue_evidence_unit, build_clue_evidence_package

    cue = make_clue_evidence_unit(
        evidence_id="ev-cue",
        chapter_id=chapters[0].id,
        narrative_chapter_number=1,
        text=chapters[0].content,
        source_start=0,
        source_end=len(chapters[0].content),
        role_hint="cue",
    )
    later = make_clue_evidence_unit(
        evidence_id="ev-later",
        chapter_id=chapters[1].id,
        narrative_chapter_number=3,
        text=chapters[1].content,
        source_start=0,
        source_end=len(chapters[1].content),
        role_hint="later",
    )
    package = build_clue_evidence_package(
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_id="clue-cand-seal",
        source_snapshot_hash=HEX_A,
        hierarchy_build_id=f"clue-build-{novel.id}",
        hierarchy_checksum=HEX_B,
        cue_units=[cue],
        later_units=[later],
        timeline_version_id=None,
        timeline_checksum=None,
    )
    draft = ClueCandidateDraft(
        candidate_id="clue-cand-seal",
        owner_id=owner.id,
        novel_id=novel.id,
        package=package,
        reason_codes=["lexical"],
    )

    class FakeRecall:
        async def build_candidates_from_nodes(self, **kwargs):
            from app.services.clues.candidates import CandidateRecallResult

            return CandidateRecallResult(drafts=[draft], hierarchy_build_id=package.hierarchy_build_id)

    judgment = _judgment_for_candidate("clue-cand-seal", "ev-cue", "ev-later")
    runtime = await _runtime(db_session, outputs={"clue-cand-seal": judgment})
    runtime.recall = FakeRecall()

    async def dispatch(run_id: int) -> None:
        await run_clue_worker(run_id, runtime=runtime)

    monkeypatch.setattr(clues_api, "dispatch_clue_run", dispatch)

    owner_id, novel_id = owner.id, novel.id
    response = await auth_client.post(f"/api/clues/{novel_id}/start-or-resume")
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]

    db_session.expire_all()
    run = await db_session.get(ClueAnalysisRun, run_id)
    assert run is not None
    assert run.status == "completed", (run.status, run.status_reason, run.progress, run.checkpoint)
    version_id = run.version_id
    pointer = await db_session.scalar(
        select(ClueActivePointer).where(
            ClueActivePointer.owner_id == owner_id,
            ClueActivePointer.novel_id == novel_id,
        )
    )
    assert pointer is not None
    assert pointer.version_id == version_id
    all_clues = list((await db_session.scalars(select(MachineClue))).all())
    clues = list(
        (
            await db_session.scalars(
                select(MachineClue).where(MachineClue.version_id == version_id)
            )
        ).all()
    )
    assert len(clues) == 1, {
        "version_id": version_id,
        "all_clues": [(c.id, c.version_id, c.logical_clue_id) for c in all_clues],
        "checkpoint": run.checkpoint,
        "progress": run.progress,
    }
    assert clues[0].logical_clue_id == "clue-cand-seal"

    # Restart: completed run should not re-call.
    again = await auth_client.post(f"/api/clues/{novel_id}/start-or-resume")
    assert again.status_code == 200
    assert again.json()["id"] == run_id
    attempts = await db_session.scalar(
        select(func.count(ClueModelCallAttempt.id)).where(
            ClueModelCallAttempt.run_id == run_id
        )
    )
    # Cache hits only (deterministic_as_cache); no started/succeeded provider rows.
    hit_rows = list(
        (
            await db_session.scalars(
                select(ClueModelCallAttempt).where(
                    ClueModelCallAttempt.run_id == run_id,
                    ClueModelCallAttempt.status == "cache_hit",
                )
            )
        ).all()
    )
    assert len(hit_rows) >= 1
    assert attempts == len(hit_rows)


@pytest.mark.asyncio
async def test_unknown_pricing_pauses_before_provider_call(db_session, monkeypatch):
    owner, novel, chapters = await _seed_hierarchy(db_session, title="Budget novel")
    from app.services.clues.candidates import ClueCandidateDraft, CandidateRecallResult
    from app.services.clues.evidence import make_clue_evidence_unit, build_clue_evidence_package

    cue = make_clue_evidence_unit(
        evidence_id="ev-b",
        chapter_id=chapters[0].id,
        narrative_chapter_number=1,
        text="hint",
        role_hint="cue",
    )
    package = build_clue_evidence_package(
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_id="cand-budget",
        source_snapshot_hash=HEX_A,
        hierarchy_build_id=f"clue-build-{novel.id}",
        hierarchy_checksum=HEX_B,
        cue_units=[cue],
        later_units=[],
        timeline_version_id=None,
        timeline_checksum=None,
    )
    draft = ClueCandidateDraft(
        candidate_id="cand-budget",
        owner_id=owner.id,
        novel_id=novel.id,
        package=package,
    )

    class FakeRecall:
        async def build_candidates_from_nodes(self, **kwargs):
            return CandidateRecallResult(drafts=[draft])

    runtime = await _runtime(
        db_session,
        deployment=_deployment(unknown_price=True),
        outputs={},  # force reserve path
    )
    runtime.deterministic_as_cache = False
    runtime.recall = FakeRecall()

    # Inject judge that would call if budget allowed.
    async def boom_judge(package, repair=False):
        raise AssertionError("provider must not be called")

    runtime.judge.judge_package = boom_judge  # type: ignore[method-assign]

    run = ClueAnalysisRun(
        owner_id=owner.id,
        novel_id=novel.id,
        active_key="active",
        status="pending",
        progress={},
    )
    db_session.add(run)
    await db_session.commit()
    run_id, novel_id = run.id, novel.id

    await run_clue_worker(run_id, runtime=runtime)
    db_session.expire_all()
    refreshed = await db_session.get(ClueAnalysisRun, run_id)
    assert refreshed is not None
    assert refreshed.status == "paused_budget"
    pointer = await db_session.scalar(
        select(ClueActivePointer).where(ClueActivePointer.novel_id == novel_id)
    )
    assert pointer is None


@pytest.mark.asyncio
async def test_stale_cas_failed_candidate_and_rollback(db_session):
    owner, novel, chapters = await _seed_hierarchy(db_session, title="CAS novel")

    async def _make_version(key: str, logical: str) -> ClueAnalysisVersion:
        version = ClueAnalysisVersion(
            owner_id=owner.id,
            novel_id=novel.id,
            version_key=key,
            status="candidate",
            source_snapshot_hash=HEX_A,
            hierarchy_build_id="b",
            hierarchy_checksum=HEX_B,
            prompt_hash=HEX_C,
            schema_hash=HEX_A,
            decoding_hash=HEX_B,
            config_hash=HEX_C,
            policy_hash=HEX_A,
            model_lineage={},
            price_snapshot={},
            manifest={},
        )
        db_session.add(version)
        await db_session.flush()
        machine = MachineClue(
            owner_id=owner.id,
            novel_id=novel.id,
            version_id=version.id,
            logical_clue_id=logical,
            title=logical,
            summary="",
            package_hash=HEX_A,
            package_snapshot={},
            confidence=0.9,
            publication_status="published",
            first_cue_chapter=1,
            first_cue_source_start=0,
        )
        db_session.add(machine)
        await db_session.flush()
        from app.models.clue import ClueEvidenceRef

        db_session.add(
            ClueEvidenceRef(
                owner_id=owner.id,
                novel_id=novel.id,
                version_id=version.id,
                logical_clue_id=logical,
                machine_clue_id=machine.id,
                role="cue",
                evidence_id="ev-stable",
                evidence_identity=f"ev-stable:{chapters[0].id}:0:5:{HEX_A}",
                chapter_id=chapters[0].id,
                narrative_chapter_number=1,
                source_start=0,
                source_end=5,
                content_hash=HEX_A,
            )
        )
        await db_session.flush()
        manifest, checksum = await snapshot_manifest(db_session, version.id)
        version.manifest = manifest
        version.manifest_checksum = checksum
        version.status = "validated"
        return version

    v1 = await _make_version("v1", "clue-old")
    v2 = await _make_version("v2", "clue-new")
    bad = await _make_version("bad", "clue-bad")
    bad.manifest_checksum = "0" * 64
    await db_session.commit()
    owner_id, novel_id = owner.id, novel.id
    v1_id, v2_id, bad_id = v1.id, v2.id, bad.id

    pointer = await promote_version(
        db_session,
        owner_id=owner_id,
        novel_id=novel_id,
        candidate_version_id=v1_id,
        expected_revision=0,
    )
    assert pointer.revision == 1
    v1_checksum = pointer.manifest_checksum

    await promote_version(
        db_session,
        owner_id=owner_id,
        novel_id=novel_id,
        candidate_version_id=v2_id,
        expected_revision=1,
    )
    with pytest.raises(StalePointerError):
        await promote_version(
            db_session,
            owner_id=owner_id,
            novel_id=novel_id,
            candidate_version_id=v1_id,
            expected_revision=1,
        )
    with pytest.raises(ManifestValidationError):
        await promote_version(
            db_session,
            owner_id=owner_id,
            novel_id=novel_id,
            candidate_version_id=bad_id,
            expected_revision=2,
        )

    rolled = await rollback_version(
        db_session,
        owner_id=owner_id,
        novel_id=novel_id,
        target_version_id=v1_id,
        expected_revision=2,
    )
    assert rolled.version_id == v1_id
    assert rolled.manifest_checksum == v1_checksum
