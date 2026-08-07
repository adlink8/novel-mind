"""Unit tests for app.services.knowledge_units.promotion edge branches.

Existing coverage (tests/test_knowledge_unit_promotion.py) handles the happy
path; this file targets the remaining rejection and state-transition branches:
missing/non-candidate builds, domain evidence set, signed report failures,
reconcile binding, pointer/watermark capture, and the commit update path.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.unit

from app.models import Novel, User
from app.models.knowledge_unit import (
    NarrativeActivePointer,
    NarrativeIndexBuild,
    NarrativeSourceSnapshot,
    NarrativeSourceWatermark,
)
from app.services.knowledge_units.eval import sign_run
from app.services.knowledge_units.promotion import (
    PromotionError,
    _verify_promotion_envelope,
    narrative_promotion_service,
)

SECRET = "promo-unit-secret"


# ── minimal fixtures ──


async def _mk_snapshot(db: AsyncSession, *, domain: str = "fiction"):
    user = User(
        username=f"promo_{domain}",
        email=f"promo_{domain}@example.com",
        hashed_password="x",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    novel = Novel(owner_id=user.id, title="promo novel", status="ready")
    db.add(novel)
    await db.flush()
    snapshot = NarrativeSourceSnapshot(
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile=domain,
        ontology_profile=f"{domain}.v1",
        status="frozen",
        source_watermark="wm",
        manifest_checksum="s" * 64,
        item_count=1,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def _mk_build(
    db: AsyncSession,
    *,
    snapshot: NarrativeSourceSnapshot,
    build_key: str,
    status: str,
    checksum: str,
    collection: str,
    domain: str = "fiction",
) -> NarrativeIndexBuild:
    build = NarrativeIndexBuild(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        source_snapshot_id=snapshot.id,
        domain_profile=domain,
        build_key=build_key,
        status=status,
        manifest_checksum=checksum,
        config_checksum="c" * 64,
        unit_count=0,
        collection_name=collection,
    )
    db.add(build)
    await db.flush()
    return build


def _eval_report(build: NarrativeIndexBuild) -> dict:
    report = {
        "run_id": f"run-{build.id}",
        "passed": True,
        "domain": build.domain_profile,
        "dataset_hash": "e" * 64,
        "build_id": build.id,
        "candidate_checksum": build.manifest_checksum,
        "collection": build.collection_name,
        "owner_id": build.owner_id,
        "novel_id": build.novel_id,
        "outputs": [{"id": "q1"}],
        "faithfulness_failures": 0,
        "canary": {"passed": True},
    }
    report["signature"] = sign_run(report, SECRET)
    return report


def _reconcile(build: NarrativeIndexBuild) -> dict:
    return {
        **{
            key: []
            for key in (
                "missing",
                "orphan",
                "duplicate",
                "wrong_build",
                "wrong_owner",
                "deleted",
                "deprecated",
            )
        },
        "build_id": build.id,
        "collection": build.collection_name,
    }


async def _prepare(db, build) -> object:
    return await narrative_promotion_service.prepare(
        db,
        candidate_build_id=build.id,
        candidate_checksum=build.manifest_checksum,
        eval_report=_eval_report(build),
        reconcile_report=_reconcile(build),
        approved_by="owner",
        evidence_secret=SECRET,
    )


# ── prepare rejections ──


async def test_prepare_rejects_missing_build(db_session):
    with pytest.raises(PromotionError, match="missing or not candidate"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=999999,
            candidate_checksum="c" * 64,
            eval_report={},
            reconcile_report={},
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_prepare_rejects_non_candidate_status(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="draft-key",
        status="draft",
        checksum="c" * 64,
        collection="cand",
    )
    with pytest.raises(PromotionError, match="missing or not candidate"):
        await _prepare(db_session, build)


async def test_prepare_rejects_missing_collection_name(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="no-coll",
        status="candidate",
        checksum="c" * 64,
        collection=None,
    )
    with pytest.raises(PromotionError, match="missing or not candidate"):
        await _prepare(db_session, build)


async def test_prepare_rejects_empty_evidence_secret(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="no-secret",
        status="candidate",
        checksum="c" * 64,
        collection="cand",
    )
    with pytest.raises(PromotionError, match="domain evidence"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=_eval_report(build),
            reconcile_report=_reconcile(build),
            approved_by="owner",
            evidence_secret="",
        )


async def test_prepare_rejects_non_fiction_history_domain(db_session):
    # Non-fiction/history domain requires BOTH fiction and history reports.
    snapshot = await _mk_snapshot(db_session, domain="mystery")
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="mystery-key",
        status="candidate",
        checksum="c" * 64,
        collection="cand",
        domain="mystery",
    )
    with pytest.raises(PromotionError, match="domain evidence"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=_eval_report(build),
            reconcile_report=_reconcile(build),
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_prepare_rejects_evaluation_not_passed(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="not-passed",
        status="candidate",
        checksum="c" * 64,
        collection="cand",
    )
    report = _eval_report(build)
    report["passed"] = False
    with pytest.raises(PromotionError, match="evaluation evidence is invalid"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=report,
            reconcile_report=_reconcile(build),
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_prepare_rejects_canary_not_passed(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="canary-fail",
        status="candidate",
        checksum="c" * 64,
        collection="cand",
    )
    report = _eval_report(build)
    report["canary"]["passed"] = False
    with pytest.raises(PromotionError, match="evaluation evidence is invalid"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=report,
            reconcile_report=_reconcile(build),
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_prepare_rejects_faithfulness_failures(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="faithfulness",
        status="candidate",
        checksum="c" * 64,
        collection="cand",
    )
    report = _eval_report(build)
    report["faithfulness_failures"] = 1
    report["signature"] = sign_run(report, SECRET)  # re-sign after mutation
    with pytest.raises(PromotionError, match="belongs to another candidate"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=report,
            reconcile_report=_reconcile(build),
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_prepare_rejects_empty_outputs(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="no-outputs",
        status="candidate",
        checksum="c" * 64,
        collection="cand",
    )
    report = _eval_report(build)
    report["outputs"] = []
    report["signature"] = sign_run(report, SECRET)  # re-sign after mutation
    with pytest.raises(PromotionError, match="belongs to another candidate"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=report,
            reconcile_report=_reconcile(build),
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_prepare_rejects_reconcile_binding_mismatch(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="reconcile-bind",
        status="candidate",
        checksum="c" * 64,
        collection="cand",
    )
    reconcile = _reconcile(build)
    reconcile["build_id"] = 12345
    with pytest.raises(PromotionError, match="binding mismatch"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=_eval_report(build),
            reconcile_report=reconcile,
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_prepare_captures_pointer_and_watermark_in_before(db_session):
    snapshot = await _mk_snapshot(db_session)
    previous = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="prev",
        status="active",
        checksum="p" * 64,
        collection="prev_col",
    )
    pointer = NarrativeActivePointer(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        build_id=previous.id,
        pointer_version=1,
        active_manifest_checksum="p" * 64,
        activated_at=datetime.now(UTC),
    )
    db_session.add(pointer)
    watermark = NarrativeSourceWatermark(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        snapshot_id=snapshot.id,
        build_id=previous.id,
        source_watermark="wm",
        manifest_checksum="p" * 64,
    )
    db_session.add(watermark)
    await db_session.flush()

    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="cand",
        status="candidate",
        checksum="c" * 64,
        collection="cand_col",
    )
    journal = await _prepare(db_session, build)
    assert journal.previous_build_id == previous.id
    assert journal.previous_checksum == "p" * 64
    before = journal.details["before"]
    assert before["build_id"] == previous.id
    assert before["watermark"]["snapshot_id"] == snapshot.id
    assert before["watermark"]["manifest_checksum"] == "p" * 64


# ── _verify_promotion_envelope ──


async def _valid_journal(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="envelope-valid",
        status="candidate",
        checksum="c" * 64,
        collection="cand_col",
    )
    journal = await _prepare(db_session, build)
    return journal, build


async def test_verify_envelope_rejects_bad_schema_version(db_session):
    journal, build = await _valid_journal(db_session)
    envelope = journal.details["promotion_evidence"]
    envelope["schema_version"] = "promotion-evidence.v9"
    with pytest.raises(PromotionError, match="envelope is invalid"):
        _verify_promotion_envelope(envelope, secret=SECRET, build=build)


async def test_verify_envelope_rejects_candidate_lineage_mismatch(db_session):
    journal, build = await _valid_journal(db_session)
    envelope = journal.details["promotion_evidence"]
    envelope["candidate"]["build_id"] = 555
    envelope["signature"] = sign_run(envelope, SECRET)
    with pytest.raises(PromotionError, match="lineage mismatch"):
        _verify_promotion_envelope(envelope, secret=SECRET, build=build)


async def test_verify_envelope_rejects_invalid_approval(db_session):
    journal, build = await _valid_journal(db_session)
    envelope = journal.details["promotion_evidence"]
    envelope["approval"] = {"identity": "", "approved_at": ""}
    envelope["signature"] = sign_run(envelope, SECRET)
    with pytest.raises(PromotionError, match="approval is invalid"):
        _verify_promotion_envelope(envelope, secret=SECRET, build=build)


async def test_verify_envelope_rejects_missing_domain_runs(db_session):
    journal, build = await _valid_journal(db_session)
    envelope = journal.details["promotion_evidence"]
    envelope["domain_evaluations"] = []
    envelope["signature"] = sign_run(envelope, SECRET)
    with pytest.raises(PromotionError, match="domain runs are missing"):
        _verify_promotion_envelope(envelope, secret=SECRET, build=build)


async def test_verify_envelope_rejects_tampered_domain_run(db_session):
    journal, build = await _valid_journal(db_session)
    envelope = journal.details["promotion_evidence"]
    envelope["domain_evaluations"][0]["run_id"] = "tampered-run"
    envelope["signature"] = sign_run(envelope, SECRET)
    with pytest.raises(PromotionError, match="domain run is invalid"):
        _verify_promotion_envelope(envelope, secret=SECRET, build=build)


# ── commit rejections and update path ──


async def test_commit_rejects_missing_journal(db_session):
    with pytest.raises(PromotionError, match="missing or not prepared"):
        await narrative_promotion_service.commit(
            db_session,
            journal_id=999999,
            candidate_checksum="c" * 64,
            evidence_secret=SECRET,
        )


async def test_commit_rejects_journal_not_prepared(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="committed-journal",
        status="active",
        checksum="c" * 64,
        collection="cand_col",
    )
    from app.models.knowledge_unit import NarrativePromotionJournal

    journal = NarrativePromotionJournal(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        transaction_key="tx-key-committed",
        candidate_build_id=build.id,
        previous_build_id=None,
        status="committed",
        candidate_checksum="c" * 64,
        previous_checksum=None,
        details={"promotion_evidence": {}},
    )
    db_session.add(journal)
    await db_session.flush()
    with pytest.raises(PromotionError, match="missing or not prepared"):
        await narrative_promotion_service.commit(
            db_session,
            journal_id=journal.id,
            candidate_checksum="c" * 64,
            evidence_secret=SECRET,
        )


async def test_commit_rejects_candidate_changed_after_prepare(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="mutated",
        status="candidate",
        checksum="c" * 64,
        collection="cand_col",
    )
    journal = await _prepare(db_session, build)
    build.status = "draft"
    await db_session.flush()
    with pytest.raises(PromotionError, match="candidate changed after prepare"):
        await narrative_promotion_service.commit(
            db_session,
            journal_id=journal.id,
            candidate_checksum=build.manifest_checksum,
            evidence_secret=SECRET,
        )


async def test_commit_rejects_pointer_changed_after_prepare(db_session):
    snapshot = await _mk_snapshot(db_session)
    previous = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="prev-pointer",
        status="active",
        checksum="p" * 64,
        collection="prev_col",
    )
    pointer = NarrativeActivePointer(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        build_id=previous.id,
        pointer_version=1,
        active_manifest_checksum="p" * 64,
        activated_at=datetime.now(UTC),
    )
    db_session.add(pointer)
    await db_session.flush()
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="cand-pointer",
        status="candidate",
        checksum="c" * 64,
        collection="cand_col",
    )
    journal = await _prepare(db_session, build)
    # Tamper with the pointer checksum between prepare and commit.
    pointer.active_manifest_checksum = "forged" * 10 + "x" * 4
    await db_session.flush()
    with pytest.raises(PromotionError, match="active pointer changed"):
        await narrative_promotion_service.commit(
            db_session,
            journal_id=journal.id,
            candidate_checksum=build.manifest_checksum,
            evidence_secret=SECRET,
        )


async def test_commit_updates_existing_pointer_and_deprecates_previous(db_session):
    snapshot = await _mk_snapshot(db_session)
    previous = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="prev-dep",
        status="active",
        checksum="p" * 64,
        collection="prev_col",
    )
    pointer = NarrativeActivePointer(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        build_id=previous.id,
        pointer_version=1,
        active_manifest_checksum="p" * 64,
        activated_at=datetime.now(UTC),
    )
    db_session.add(pointer)
    await db_session.flush()

    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="cand-dep",
        status="candidate",
        checksum="c" * 64,
        collection="cand_col",
    )
    journal = await _prepare(db_session, build)
    result = await narrative_promotion_service.commit(
        db_session,
        journal_id=journal.id,
        candidate_checksum=build.manifest_checksum,
        evidence_secret=SECRET,
    )
    assert result.build_id == build.id
    assert result.pointer_version == 2
    assert result.active_manifest_checksum == "c" * 64
    assert build.status == "active"
    assert journal.status == "committed"
    assert (
        await db_session.get(NarrativeIndexBuild, previous.id)
    ).status == "deprecated"
