"""Phase 33-03 consistency evaluator unit tests (REQ-VIS-04, D-33-04).

Covers the frozen-fixture evaluator contract and the durable report service:
- identity drift / style drift / unsupported detail / unavailable are four
  distinguishable verdicts with replayable evidence;
- a score is a review signal in [0, 1], never canon — the contract has no
  auto-approve / promote / Visual-Bible-rewrite surface;
- the fixture set is frozen (extra=forbid, immutable) and its hash is
  sensitive to any attribute change;
- report idempotency keys replay from owner/novel/asset/report_key/evidence;
- append-only report rows and owner-scoped idempotent persistence.

The DB-backed tests use the SQLite in-memory ``db_session`` (same as 33-01
``test_contracts.py``); no external service is required.
"""

from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Novel, User
from app.models.illustration import AssetRevision, ConsistencyReport
from app.models.illustration_job import IllustrationJob
from app.schemas.illustration import (
    ConsistencyReportContract,
    IllustrationConsistencyVerdict,
)
from app.services.illustrations.consistency import (
    CandidateConsistencyEvidence,
    ConsistencyEvaluator,
    ConsistencyPolicy,
    ConsistencyReportConflict,
    ConsistencyReportNotFound,
    ConsistencyReportService,
    FrozenCharacterFixture,
    mock_consistency_fixture_registry,
    report_view,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64

_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nmock-image-bytes-" + b"x" * 32


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _evidence(**overrides) -> CandidateConsistencyEvidence:
    payload = {
        "character_key": "arin",
        "scene_key": "ch1",
        "identity_attributes": (
            "black_hair",
            "amber_eyes",
            "lean_build",
            "scar_left_brow",
        ),
        "style_attributes": ("ink_painting", "warm_palette", "soft_lighting"),
        "negative_constraints_present": (),
    }
    payload.update(overrides)
    return CandidateConsistencyEvidence.model_validate(payload)


def _evaluate(
    evaluator: ConsistencyEvaluator,
    *,
    owner_id: int = 11,
    novel_id: int = 22,
    asset_revision_id: int = 1,
    report_key: str = "arin:ch1",
    evidence: CandidateConsistencyEvidence | None = None,
) -> ConsistencyReportContract:
    return evaluator.evaluate(
        owner_id=owner_id,
        novel_id=novel_id,
        asset_revision_id=asset_revision_id,
        report_key=report_key,
        evidence=evidence or _evidence(),
    )


# ---------------------------------------------------------------------------
# Fixture contract: frozen, strict, hash-sensitive
# ---------------------------------------------------------------------------


def test_fixture_contract_is_frozen_and_strict():
    fixture = mock_consistency_fixture_registry()["arin"]
    assert fixture.character_key == "arin"
    assert fixture.reference_asset_ids == ("ref-char-arin-1", "ref-char-arin-2")
    with pytest.raises(ValidationError):
        FrozenCharacterFixture.model_validate(
            {**fixture.model_dump(), "unexpected_field": True}
        )
    with pytest.raises(ValidationError):
        FrozenCharacterFixture.model_validate(
            {**fixture.model_dump(), "character_key": ""}
        )


def test_policy_requires_monotone_thresholds():
    ConsistencyPolicy()  # defaults are valid
    with pytest.raises(ValidationError):
        ConsistencyPolicy(identity_fail_below=0.95, identity_concern_below=0.6)
    with pytest.raises(ValidationError):
        ConsistencyPolicy(style_fail_below=0.8, style_concern_below=0.7)


def test_fixture_set_hash_is_hash_sensitive():
    base = mock_consistency_fixture_registry()
    mutated = {
        "arin": base["arin"].model_copy(
            update={
                "identity_attributes": base["arin"].identity_attributes
                + ("grey_hair",)
            }
        )
    }
    base_report = _evaluate(ConsistencyEvaluator(base))
    mutated_report = _evaluate(ConsistencyEvaluator(mutated))
    assert mutated_report.fixture_set_hash != base_report.fixture_set_hash
    assert len(mutated_report.fixture_set_hash) == 64


# ---------------------------------------------------------------------------
# Evaluator verdicts: identity / style / unsupported / unavailable
# ---------------------------------------------------------------------------


def test_pass_when_candidate_matches_fixture():
    report = _evaluate(ConsistencyEvaluator(mock_consistency_fixture_registry()))
    assert report.verdict is IllustrationConsistencyVerdict.PASS
    assert report.scores["identity"] == 1.0
    assert report.scores["style"] == 1.0
    assert report.details["drift"]["identity"]["missing"] == []
    assert report.details["drift"]["unsupported_details"] == []
    assert report.details["status"] == "evaluated"


def test_identity_drift_is_concern_and_distinguishable():
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    report = _evaluate(
        evaluator,
        evidence=_evidence(
            identity_attributes=("black_hair", "lean_build", "scar_left_brow")
        ),
    )
    assert report.verdict is IllustrationConsistencyVerdict.CONCERN
    assert report.scores["identity"] == pytest.approx(3 / 4)
    assert "amber_eyes" in report.details["drift"]["identity"]["missing"]


def test_style_drift_below_fail_threshold_fails():
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    report = _evaluate(
        evaluator, evidence=_evidence(style_attributes=("ink_painting",))
    )
    assert report.verdict is IllustrationConsistencyVerdict.FAIL
    assert report.scores["style"] == pytest.approx(1 / 3, abs=1e-3)


def test_negative_constraint_violation_fails_closed():
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    report = _evaluate(
        evaluator, evidence=_evidence(negative_constraints_present=("no_glasses",))
    )
    assert report.verdict is IllustrationConsistencyVerdict.FAIL
    assert report.details["drift"]["negative_constraints"]["violated"] == ["no_glasses"]
    assert report.scores["negative_constraint_violations"] == 1


def test_unsupported_detail_is_recorded_as_concern():
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    report = _evaluate(
        evaluator,
        evidence=_evidence(
            identity_attributes=(
                "black_hair",
                "amber_eyes",
                "lean_build",
                "scar_left_brow",
                "golden_armor",
            )
        ),
    )
    assert report.verdict is IllustrationConsistencyVerdict.CONCERN
    assert "golden_armor" in report.details["drift"]["unsupported_details"]
    assert report.scores["unsupported_detail_count"] == 1


def test_unavailable_when_no_evaluator_fixture():
    report = _evaluate(ConsistencyEvaluator())
    assert report.verdict is IllustrationConsistencyVerdict.UNAVAILABLE
    assert report.details["reason_code"] == "fixture_missing"
    assert report.scores == {}
    assert report.reference_asset_ids == []


def test_unavailable_for_unknown_character():
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    report = _evaluate(evaluator, evidence=_evidence(character_key="lin"))
    assert report.verdict is IllustrationConsistencyVerdict.UNAVAILABLE
    assert report.details["reason_code"] == "fixture_missing"


def test_scores_are_review_signals_not_canon():
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    report = _evaluate(evaluator)
    assert 0.0 <= report.scores["identity"] <= 1.0
    fields = set(ConsistencyReportContract.model_fields)
    assert "auto_approve" not in fields
    assert "promote_to_canon" not in fields
    assert "rewrite_visual_bible" not in fields
    assert "canon" not in fields
    assert report.verdict in set(IllustrationConsistencyVerdict)


def test_evaluator_is_deterministic_and_replays():
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    first = _evaluate(evaluator)
    second = _evaluate(evaluator)
    assert first.idempotency_key == second.idempotency_key
    assert first.scores == second.scores
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_idempotency_key_changes_on_evidence_report_key_or_scope():
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    base = _evaluate(evaluator)
    assert (
        _evaluate(evaluator, evidence=_evidence(identity_attributes=("black_hair",))).idempotency_key
        != base.idempotency_key
    )
    assert _evaluate(evaluator, report_key="arin:ch2").idempotency_key != base.idempotency_key
    assert _evaluate(evaluator, owner_id=12).idempotency_key != base.idempotency_key
    assert _evaluate(evaluator, asset_revision_id=2).idempotency_key != base.idempotency_key


# ---------------------------------------------------------------------------
# Durable report service: idempotent persistence, owner scope, append-only
# ---------------------------------------------------------------------------


async def _persist_asset(db_session: AsyncSession, username: str):
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()
    novel = Novel(title=f"Consistency Novel {username}", owner_id=user.id)
    db_session.add(novel)
    await db_session.flush()
    job = IllustrationJob(
        owner_id=user.id,
        novel_id=novel.id,
        job_key="job-cons",
        idempotency_key=HEX64,
        status="succeeded",
        status_reason="generated",
        error_code=None,
        lease_id=None,
        lease_expires_at=None,
        heartbeat_at=None,
        cancel_requested=False,
        retry_count=0,
        scene_spec_hash="1" * 64,
        prompt_revision_id=101,
        prompt_revision_hash="2" * 64,
        visual_bible_revision_id=None,
        visual_bible_revision_hash="3" * 64,
        source_snapshot_id="ss-1",
        source_snapshot_hash="4" * 64,
        cutoff_chapter=8,
        model_lineage={"provider": "mock", "model": "mock-img-v1"},
        config_hash="5" * 64,
        price_snapshot={},
        response_hash=None,
        schema_version="illustration.v1",
    )
    db_session.add(job)
    await db_session.flush()
    asset = AssetRevision(
        owner_id=user.id,
        novel_id=novel.id,
        job_id=job.id,
        revision_key="rev-1",
        revision_number=1,
        asset_id="asset-cons-1",
        storage_key=f"assets/{user.id}/{novel.id}/{HEX64}.png",
        mime_type="image/png",
        width=1024,
        height=1024,
        size_bytes=len(_IMAGE_BYTES),
        bytes_hash=sha256(_IMAGE_BYTES).hexdigest(),
        scene_spec_hash="1" * 64,
        prompt_revision_id=101,
        prompt_revision_hash="2" * 64,
        visual_bible_revision_hash="3" * 64,
        source_snapshot_id="ss-1",
        source_snapshot_hash="4" * 64,
        cutoff_chapter=8,
        model_lineage={"provider": "mock", "model": "mock-img-v1"},
        config_hash="5" * 64,
        provider="mock",
        provider_model="mock-img-v1",
        provider_request_id="req-1",
        provider_response={},
        provenance={},
        rights_status="unreviewed",
        approval_state="candidate",
        approved_by=None,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64,
        projection_hash=HEX64,
        schema_version="illustration-asset.v1",
    )
    db_session.add(asset)
    await db_session.flush()
    return asset, job, user, novel


def _service(db_session: AsyncSession) -> ConsistencyReportService:
    return ConsistencyReportService(
        db_session, evaluator=ConsistencyEvaluator(mock_consistency_fixture_registry())
    )


async def test_report_persists_with_full_lineage_and_replays(db_session: AsyncSession):
    asset, _, _, _ = await _persist_asset(db_session, "ill_cons_persist")
    service = _service(db_session)

    row, replayed = await service.evaluate(
        owner_id=asset.owner_id,
        novel_id=asset.novel_id,
        asset_revision_id=asset.id,
        report_key="arin:ch1",
        evidence=_evidence(),
    )
    assert replayed is False
    assert row.verdict == "pass"
    assert row.evaluator_id == "illustration-consistency.fixture.v1"
    assert len(row.fixture_set_hash) == 64
    assert len(row.idempotency_key) == 64
    assert row.reference_asset_ids == ["ref-char-arin-1", "ref-char-arin-2"]
    # The report freezes the candidate source/prompt/model lineage (D-33-04).
    assert row.details["asset"]["scene_spec_hash"] == asset.scene_spec_hash
    assert row.details["asset"]["source_snapshot_hash"] == asset.source_snapshot_hash
    assert row.details["asset"]["cutoff_chapter"] == asset.cutoff_chapter
    assert row.details["asset"]["model_lineage"] == asset.model_lineage

    # Same report_key + same evidence replays the same row (idempotent).
    again, replayed = await service.evaluate(
        owner_id=asset.owner_id,
        novel_id=asset.novel_id,
        asset_revision_id=asset.id,
        report_key="arin:ch1",
        evidence=_evidence(),
    )
    assert again.id == row.id
    assert replayed is True
    assert await db_session.scalar(select(ConsistencyReport)) is row

    # A different evaluation under the same report_key fails closed.
    with pytest.raises(ConsistencyReportConflict):
        await service.evaluate(
            owner_id=asset.owner_id,
            novel_id=asset.novel_id,
            asset_revision_id=asset.id,
            report_key="arin:ch1",
            evidence=_evidence(identity_attributes=("black_hair",)),
        )


async def test_report_row_is_append_only(db_session: AsyncSession):
    asset, _, _, _ = await _persist_asset(db_session, "ill_cons_append")
    service = _service(db_session)
    row, _ = await service.evaluate(
        owner_id=asset.owner_id,
        novel_id=asset.novel_id,
        asset_revision_id=asset.id,
        report_key="arin:ch1",
        evidence=_evidence(),
    )
    row.verdict = "fail"
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_service_is_owner_scoped(db_session: AsyncSession):
    asset, _, owner, novel = await _persist_asset(db_session, "ill_cons_scope")
    service = _service(db_session)
    with pytest.raises(ConsistencyReportNotFound):
        await service.evaluate(
            owner_id=999,
            novel_id=novel.id,
            asset_revision_id=asset.id,
            report_key="arin:ch1",
            evidence=_evidence(),
        )
    with pytest.raises(ConsistencyReportNotFound):
        await service.evaluate(
            owner_id=owner.id,
            novel_id=999,
            asset_revision_id=asset.id,
            report_key="arin:ch1",
            evidence=_evidence(),
        )


async def test_report_view_loads(db_session: AsyncSession):
    asset, _, _, _ = await _persist_asset(db_session, "ill_cons_view")
    service = _service(db_session)
    row, _ = await service.evaluate(
        owner_id=asset.owner_id,
        novel_id=asset.novel_id,
        asset_revision_id=asset.id,
        report_key="arin:ch1",
        evidence=_evidence(),
    )
    view = report_view(row)
    assert view.verdict is IllustrationConsistencyVerdict.PASS
    assert view.owner_id == asset.owner_id
    assert view.reference_asset_ids == ["ref-char-arin-1", "ref-char-arin-2"]
    assert view.evaluator_id == "illustration-consistency.fixture.v1"
    assert view.created_at is not None
