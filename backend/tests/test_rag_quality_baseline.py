"""06-09 baseline prepare/commit + cross-chunker report tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import BaselineCandidate, QualityRun
from app.models.user import User
from app.services.rag_fixture import stable_hash
from app.services.rag_quality import (
    BaselineServiceError,
    build_cross_chunker_report,
    commit_baseline_candidate,
    compute_prepare_fingerprint,
    get_active_baseline,
    make_baseline_from_metrics,
    prepare_baseline_candidate,
    recompute_chunker_config_hash,
)

pytestmark = pytest.mark.unit


async def _user(db: AsyncSession, name: str = "bl") -> User:
    user = User(username=name, email=f"{name}@test.com", hashed_password="hash")
    db.add(user)
    await db.flush()
    return user


def _metrics() -> dict:
    return {
        "context_recall_at_5_mean": 0.8,
        "answer_relevance_mean": 0.75,
        "cost_usd_total": 0.01,
        "answer_faithfulness_95lb": 0.7,
        "context_precision_mean": 0.6,
        "latency_ms_total": 120.0,
    }


async def _eligible_run(
    db: AsyncSession,
    user: User,
    *,
    job_id: str,
    chunker_name: str = "baseline-fixed",
    chunker_version: str = "1.0.0",
    config: dict | None = None,
    snap: str | None = None,
    status: str = "passed",
    comparable: bool = True,
) -> QualityRun:
    cfg = config or {"size": 256}
    cfg_hash = recompute_chunker_config_hash(cfg)
    snap_h = snap or ("d" * 64)
    man_h = stable_hash({"chunker": chunker_name, "v": chunker_version, "c": cfg})
    metrics = _metrics()
    inp = stable_hash({"job": job_id, "snap": snap_h, "cfg": cfg_hash})
    out = stable_hash({"out": job_id, "m": metrics})
    sig = "sig-" + job_id[:20] + ("x" * 40)
    sig = sig[:64] if len(sig) > 64 else sig + ("y" * (64 - len(sig)))
    row = QualityRun(
        job_id=job_id,
        owner_id=user.id,
        status=status,
        payload={},
        checkpoint={},
        stage_cache={},
        metrics=metrics if comparable else None,
        input_hash=inp if comparable else None,
        output_hash=out if comparable else None,
        report_signature=sig if comparable else None,
        chunker_name=chunker_name if comparable else None,
        chunker_version=chunker_version if comparable else None,
        chunker_config_hash=cfg_hash if comparable else None,
        chunk_manifest_hash=man_h if comparable else None,
        # Snapshot may still be known on legacy rows; incompleteness is via comparable=false
        source_snapshot_hash=snap_h,
        quality_comparable=comparable,
        incomparable_reason=None if comparable else "legacy_incomparable",
    )
    db.add(row)
    await db.flush()
    return row


@pytest.mark.asyncio
async def test_baseline_candidate_persists_prepare_evidence(db_session: AsyncSession):
    user = await _user(db_session, "prep1")
    await _eligible_run(db_session, user, job_id="job-prep-1")
    await db_session.commit()

    cand = await prepare_baseline_candidate(
        db_session, owner_id=user.id, job_id="job-prep-1"
    )
    await db_session.commit()

    assert cand["state"] == "prepared"
    assert cand["quality_run_job_id"] == "job-prep-1"
    assert cand["prepare_token"]
    assert cand["prepare_fingerprint"]
    assert cand["chunker_name"] == "baseline-fixed"
    assert cand["metrics_snapshot"]["context_recall_at_5_mean"] == 0.8

    loaded = (
        await db_session.execute(
            select(BaselineCandidate).where(BaselineCandidate.id == cand["id"])
        )
    ).scalar_one()
    assert loaded.prepare_fingerprint == cand["prepare_fingerprint"]
    assert loaded.journal[0]["event"] == "prepared"


@pytest.mark.asyncio
async def test_prepare_rejects_legacy_and_non_passed(db_session: AsyncSession):
    user = await _user(db_session, "prep_bad")
    await _eligible_run(
        db_session, user, job_id="job-legacy", comparable=False, status="passed"
    )
    await _eligible_run(
        db_session, user, job_id="job-queued", status="queued", comparable=True
    )
    await db_session.commit()

    with pytest.raises(BaselineServiceError) as e1:
        await prepare_baseline_candidate(
            db_session, owner_id=user.id, job_id="job-legacy"
        )
    assert (
        "comparable" in e1.value.message.lower() or "legacy" in e1.value.message.lower()
    )

    with pytest.raises(BaselineServiceError) as e2:
        await prepare_baseline_candidate(
            db_session, owner_id=user.id, job_id="job-queued"
        )
    assert (
        "eligible" in e2.value.message.lower() or "queued" in e2.value.message.lower()
    )


@pytest.mark.asyncio
async def test_commit_success_and_idempotent(db_session: AsyncSession):
    user = await _user(db_session, "commit_ok")
    await _eligible_run(db_session, user, job_id="job-c1", status="qualified")
    await db_session.commit()

    cand = await prepare_baseline_candidate(
        db_session, owner_id=user.id, job_id="job-c1"
    )
    await db_session.commit()

    r1 = await commit_baseline_candidate(
        db_session,
        owner_id=user.id,
        candidate_id=cand["id"],
        prepare_token=cand["prepare_token"],
    )
    await db_session.commit()
    assert r1["ok"] is True
    assert r1["candidate"]["state"] == "committed"
    assert r1["active"]["candidate_id"] == cand["id"]

    r2 = await commit_baseline_candidate(
        db_session,
        owner_id=user.id,
        candidate_id=cand["id"],
        prepare_token=cand["prepare_token"],
    )
    await db_session.commit()
    assert r2["ok"] is True
    assert r2["idempotent"] is True

    active = await get_active_baseline(db_session, owner_id=user.id)
    assert active is not None
    assert active["candidate_id"] == cand["id"]


@pytest.mark.asyncio
async def test_commit_rejects_tamper_leaves_active_unchanged(db_session: AsyncSession):
    user = await _user(db_session, "commit_tamper")
    await _eligible_run(db_session, user, job_id="job-base", status="passed")
    await db_session.commit()

    base_cand = await prepare_baseline_candidate(
        db_session, owner_id=user.id, job_id="job-base"
    )
    await db_session.commit()
    ok = await commit_baseline_candidate(
        db_session,
        owner_id=user.id,
        candidate_id=base_cand["id"],
        prepare_token=base_cand["prepare_token"],
    )
    await db_session.commit()
    assert ok["ok"] is True
    active_before = ok["active"]["candidate_id"]

    await _eligible_run(db_session, user, job_id="job-tamper", status="passed")
    await db_session.commit()
    cand2 = await prepare_baseline_candidate(
        db_session, owner_id=user.id, job_id="job-tamper"
    )
    await db_session.commit()

    # Tamper QualityRun after prepare
    run = (
        await db_session.execute(
            select(QualityRun).where(QualityRun.job_id == "job-tamper")
        )
    ).scalar_one()
    run.output_hash = "f" * 64
    await db_session.commit()

    rejected = await commit_baseline_candidate(
        db_session,
        owner_id=user.id,
        candidate_id=cand2["id"],
        prepare_token=cand2["prepare_token"],
    )
    await db_session.commit()
    assert rejected["ok"] is False
    assert rejected["candidate"]["state"] == "rejected"
    assert rejected["active"]["candidate_id"] == active_before

    active = await get_active_baseline(db_session, owner_id=user.id)
    assert active["candidate_id"] == active_before

    # History preserved — two candidates
    rows = (
        (
            await db_session.execute(
                select(BaselineCandidate).where(BaselineCandidate.owner_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {r.state for r in rows} == {"committed", "rejected"}


@pytest.mark.asyncio
async def test_cross_chunker_report_groups_same_snapshot(db_session: AsyncSession):
    user = await _user(db_session, "report1")
    snap = "a" * 64
    other = "b" * 64
    await _eligible_run(
        db_session,
        user,
        job_id="r-chunk-a",
        chunker_name="rule-v1",
        chunker_version="1.0.0",
        config={"size": 300},
        snap=snap,
    )
    await _eligible_run(
        db_session,
        user,
        job_id="r-chunk-b",
        chunker_name="sem-v1",
        chunker_version="2.0.0",
        config={"size": 400},
        snap=snap,
    )
    await _eligible_run(
        db_session,
        user,
        job_id="r-other-snap",
        chunker_name="rule-v1",
        chunker_version="1.0.0",
        config={"size": 300},
        snap=other,
    )
    await _eligible_run(
        db_session,
        user,
        job_id="r-legacy",
        comparable=False,
        snap=snap,
    )
    await db_session.commit()

    report = await build_cross_chunker_report(
        db_session, owner_id=user.id, source_snapshot_hash=snap
    )
    assert report["source_snapshot_hash"] == snap
    assert len(report["series"]) == 2
    names = {s["chunker_name"] for s in report["series"]}
    assert names == {"rule-v1", "sem-v1"}
    # Distinct series identity — no collapse
    keys = {
        (
            s["chunker_name"],
            s["chunker_version"],
            s["chunker_config_hash"],
            s["chunk_manifest_hash"],
        )
        for s in report["series"]
    }
    assert len(keys) == 2
    reasons = {e["reason"] for e in report["exclusions"]}
    assert "different_source_snapshot" in reasons or "legacy_incomparable" in reasons
    assert any(e["reason"] == "legacy_incomparable" for e in report["exclusions"])


def test_prepare_fingerprint_changes_with_lineage():
    base = dict(
        run_status="passed",
        input_hash="1" * 64,
        output_hash="2" * 64,
        report_signature="sig",
        metrics=_metrics(),
        chunker_name="a",
        chunker_version="1",
        chunker_config_hash="3" * 64,
        chunk_manifest_hash="4" * 64,
        source_snapshot_hash="5" * 64,
    )
    fp1 = compute_prepare_fingerprint(**base)
    base2 = {**base, "chunker_name": "b"}
    fp2 = compute_prepare_fingerprint(**base2)
    assert fp1 != fp2


def test_make_baseline_from_metrics_is_shape_only_not_promotion():
    m = make_baseline_from_metrics(_metrics())
    assert "context_recall_at_5_mean" in m
    # Helper must not claim promotion authority — no lineage fields
    assert "chunker_name" not in m
    assert "source_snapshot_hash" not in m
