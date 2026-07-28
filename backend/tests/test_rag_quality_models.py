"""ORM + Pydantic contracts for RAG quality fixture tables/schemas (06-03)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import (
    EvalDataset,
    QualityRun,
    RagEvalCase,
    RagFixtureJob,
    RagSourceSnapshot,
)
from app.models.novel import Novel
from app.models.user import User
from app.schemas.eval import (
    LEGACY_INCOMPARABLE_REASON,
    SCHEMA_VERSION_RAG_QUALITY,
    ChunkerLineage,
    Claim,
    EvalCase,
    EvidenceRef,
    ModelLineage,
    SnapshotChunk,
    SourceSnapshot,
)
from app.services.rag_fixture import (
    build_source_snapshot,
    content_hash,
    make_evidence_ref,
    resolve_lineage,
    schema_contract_hash,
    stable_hash,
)
from app.services.rag_quality import (
    canonicalize_chunker_lineage,
    recompute_chunker_config_hash,
)

pytestmark = pytest.mark.unit


async def _user_novel(db: AsyncSession, name: str = "ragq") -> tuple[User, Novel]:
    user = User(username=name, email=f"{name}@test.com", hashed_password="hash")
    db.add(user)
    await db.flush()
    novel = Novel(title=f"novel-{name}", owner_id=user.id)
    db.add(novel)
    await db.flush()
    return user, novel


def test_evidence_ref_requires_end_after_start():
    with pytest.raises(ValidationError):
        EvidenceRef(
            chunk_content_hash="a" * 64,
            start_offset=10,
            end_offset=5,
            quote_hash="b" * 64,
        )


def test_model_lineage_weights_revision_alias():
    started = datetime(2026, 7, 12, tzinfo=timezone.utc)
    lineage = ModelLineage.model_validate(
        {
            "provider": "ollama",
            "model_family": "qwen",
            "model_id": "qwen3.5:9b",
            "weights/revision": "rev-1",
            "prompt_hash": "c" * 64,
            "prompt_version": "v1",
            "schema_hash": "d" * 64,
            "started_at": started.isoformat(),
        }
    )
    assert lineage.weights_revision == "rev-1"
    dumped = lineage.model_dump(by_alias=True)
    assert dumped["weights/revision"] == "rev-1"


def test_eval_case_rejects_db_id_only_as_sole_truth_in_service_path():
    """Schema allows gold_chunk_db_ids for legacy, but cases must carry hash evidence."""
    case = EvalCase(
        case_id="legacy",
        snapshot_hash="e" * 64,
        question="q",
        case_type="answerable",
        gold_chunk_db_ids=[1, 2, 3],
        claims=[Claim(claim_id="c1", text="x", critical=True, evidence_set_ids=[])],
    )
    assert case.gold_chunk_db_ids == [1, 2, 3]
    assert case.equivalent_evidence_sets == []


def test_source_snapshot_schema_fields():
    snap = SourceSnapshot(
        snapshot_id="s1",
        owner_id=1,
        work_id=2,
        version="v1",
        chunks=[
            SnapshotChunk(
                content_hash=content_hash("hello"),
                text_hash=content_hash("hello"),
                length=5,
                text="hello",
            )
        ],
        manifest_hash="f" * 64,
        created_at=datetime.now(timezone.utc),
        signature="sig",
    )
    assert snap.canonicalization_version
    assert snap.chunks[0].content_hash == content_hash("hello")


@pytest.mark.asyncio
async def test_persist_snapshot_job_and_case(db_session: AsyncSession):
    user, novel = await _user_novel(db_session, "rag_models")
    built = build_source_snapshot(
        owner_id=user.id,
        work_id=novel.id,
        texts=["chunk body alpha", "chunk body beta"],
        version="v1",
    )
    row = RagSourceSnapshot(
        snapshot_id=built.snapshot_id,
        owner_id=user.id,
        work_id=novel.id,
        version=built.version,
        canonicalization_version=built.canonicalization_version,
        chunks=[
            {
                "content_hash": c.content_hash,
                "text_hash": c.text_hash,
                "length": c.length,
            }
            for c in built.chunks
        ],
        manifest_hash=built.manifest_hash,
        signature=built.signature,
        status="frozen",
    )
    db_session.add(row)
    await db_session.flush()

    job = RagFixtureJob(
        job_id="job-1",
        owner_id=user.id,
        work_id=novel.id,
        snapshot_pk=row.id,
        status="snapshot_ready",
        attempt=0,
        metrics=None,
        quality_comparable=False,
        checkpoint={},
    )
    db_session.add(job)
    await db_session.flush()

    ref = make_evidence_ref(built, built.chunks[0].content_hash, 0, 5)
    case = RagEvalCase(
        case_id="case-1",
        owner_id=user.id,
        work_id=novel.id,
        snapshot_pk=row.id,
        schema_version=SCHEMA_VERSION_RAG_QUALITY,
        snapshot_hash=built.manifest_hash,
        question="What is alpha?",
        case_type="answerable",
        claims=[
            {
                "claim_id": "c1",
                "text": "alpha",
                "critical": True,
                "evidence_set_ids": ["s1"],
            }
        ],
        equivalent_evidence_sets=[
            {
                "set_id": "s1",
                "refs": [ref.model_dump()],
            }
        ],
        reference_answer="alpha",
        fixture_hash="a" * 64,
        signature="sig",
        status="frozen",
        payload={},
    )
    db_session.add(case)
    await db_session.commit()

    loaded = await db_session.get(RagSourceSnapshot, row.id)
    assert loaded is not None
    assert loaded.manifest_hash == built.manifest_hash

    from sqlalchemy import select

    job_row = (
        await db_session.execute(
            select(RagFixtureJob).where(RagFixtureJob.job_id == "job-1")
        )
    ).scalar_one()
    assert job_row.metrics is None
    assert job_row.quality_comparable is False

    case_row = (
        await db_session.execute(
            select(RagEvalCase).where(RagEvalCase.case_id == "case-1")
        )
    ).scalar_one()
    # Truth is content hash on case, not autoincrement gold ids
    assert "chunk_content_hash" in case_row.equivalent_evidence_sets[0]["refs"][0]


@pytest.mark.asyncio
async def test_legacy_eval_dataset_still_works(db_session: AsyncSession):
    """Keep EvalDataset/Run/Result path intact (06-03 must not break legacy)."""
    user, novel = await _user_novel(db_session, "legacy_eval")
    ds = EvalDataset(
        novel_id=novel.id,
        question="legacy q",
        gold_chunks=[9, 8],
        status="candidate",
    )
    db_session.add(ds)
    await db_session.commit()
    loaded = await db_session.get(EvalDataset, ds.id)
    assert loaded is not None
    assert loaded.gold_chunks == [9, 8]


def test_resolve_lineage_requires_weights():
    with pytest.raises(Exception) as ei:
        resolve_lineage(
            provider="x",
            model_family="f",
            model_id="m",
            weights_revision=None,
            prompt_hash="a" * 64,
            prompt_version="v1",
            schema_hash=schema_contract_hash(),
        )
    assert "weights" in str(ei.value).lower() or "revision" in str(ei.value).lower()


def test_chunker_lineage_recomputes_config_hash():
    cfg = {"size": 512, "overlap": 64}
    lin = ChunkerLineage(
        chunker_name="baseline-fixed",
        chunker_version="1.0.0",
        chunker_config=cfg,
        chunker_config_hash="0" * 64,  # wrong on purpose
        chunk_manifest_hash="a" * 64,
        source_snapshot_hash="b" * 64,
    )
    canonical, err = canonicalize_chunker_lineage(lin)
    assert canonical is None
    assert err is not None and "chunker_config_hash mismatch" in err

    good = ChunkerLineage(
        chunker_name="baseline-fixed",
        chunker_version="1.0.0",
        chunker_config=cfg,
        chunker_config_hash=recompute_chunker_config_hash(cfg),
        chunk_manifest_hash="a" * 64,
        source_snapshot_hash="b" * 64,
    )
    canonical2, err2 = canonicalize_chunker_lineage(good)
    assert err2 is None
    assert canonical2 is not None
    assert canonical2.chunker_config_hash == recompute_chunker_config_hash(cfg)


def test_missing_lineage_is_legacy_incomparable_never_invented():
    canonical, reason = canonicalize_chunker_lineage(None)
    assert canonical is None
    assert reason == LEGACY_INCOMPARABLE_REASON
    canonical2, reason2 = canonicalize_chunker_lineage({})
    assert canonical2 is None
    assert reason2 == LEGACY_INCOMPARABLE_REASON


@pytest.mark.asyncio
async def test_quality_run_persist_with_lineage(db_session: AsyncSession):
    user, novel = await _user_novel(db_session, "qrun_ok")
    cfg = {"size": 256}
    cfg_hash = recompute_chunker_config_hash(cfg)
    row = QualityRun(
        job_id="qjob-persist-1",
        owner_id=user.id,
        work_id=novel.id,
        status="queued",
        payload={"hello": "world"},
        checkpoint={"stage": "queued", "committed": []},
        stage_cache={},
        input_hash=stable_hash({"x": 1}),
        chunker_name="baseline-fixed",
        chunker_version="1.0.0",
        chunker_config_hash=cfg_hash,
        chunk_manifest_hash="c" * 64,
        source_snapshot_hash="d" * 64,
        quality_comparable=True,
    )
    db_session.add(row)
    await db_session.commit()

    loaded = (
        await db_session.execute(
            select(QualityRun).where(QualityRun.job_id == "qjob-persist-1")
        )
    ).scalar_one()
    assert loaded.quality_comparable is True
    assert loaded.chunker_name == "baseline-fixed"
    assert loaded.chunker_config_hash == cfg_hash
    assert loaded.payload["hello"] == "world"


@pytest.mark.asyncio
async def test_quality_run_legacy_without_lineage_incomparable(
    db_session: AsyncSession,
):
    user, novel = await _user_novel(db_session, "qrun_legacy")
    row = QualityRun(
        job_id="qjob-legacy-1",
        owner_id=user.id,
        work_id=novel.id,
        status="passed",
        payload={},
        checkpoint={},
        stage_cache={},
        metrics={"context_recall_at_5_mean": 0.9},
        # No five-tuple — must remain incomparable; never invent hashes.
        quality_comparable=False,
        incomparable_reason=LEGACY_INCOMPARABLE_REASON,
    )
    db_session.add(row)
    await db_session.commit()
    loaded = await db_session.get(QualityRun, row.id)
    assert loaded is not None
    assert loaded.quality_comparable is False
    assert loaded.incomparable_reason == LEGACY_INCOMPARABLE_REASON
    assert loaded.chunker_name is None
    assert loaded.chunker_config_hash is None
