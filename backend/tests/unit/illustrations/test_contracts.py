"""Phase 33-01 Illustration Job and Asset contract tests (REQ-VIS-04).

Covers D-33-01..D-33-04:
- idempotent durable jobs: the idempotency key replays from the
  owner/novel/SceneSpec/prompt/model/config lineage; a duplicate request maps
  to the same job and one charge; explicit queued/running/paused/succeeded/
  failed/cancelled/outcome_unknown states; a provider failure never becomes an
  empty successful asset;
- immutable asset revisions: content bytes hash/size/MIME replay from the
  payload, immutable lineage matches the job, and the approval_state is the
  only mutable projection (candidate until explicit human approval);
- budget and cost: worst-case reservation from a price snapshot, unknown
  pricing and budget exhaustion fail closed, unknown usage/cost stays explicit;
- consistency reports are evidence with evaluator/model/fixture lineage, never
  canon and never auto-approving;
- candidate-only approval gate: review events are append-only and idempotent;
  frozen envelopes require proposal_ready + cleared rights (Phase 34 handoff);
- ORM metadata, append-only content rows and the migration chain
  (20260801_illustration_jobs on top of 20260801_prompt_review_events).
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
from app.models.user import User
from app.models.illustration_job import (
    IllustrationAttempt,
    IllustrationBudgetLedger,
    IllustrationBudgetReservation,
    IllustrationJob,
    IllustrationReviewEvent,
)
from app.models.illustration import AssetRevision, ConsistencyReport
from app.schemas.illustration import (
    ILLUSTRATION_ACTION_TO_STATE,
    ILLUSTRATION_APPROVAL_STATES,
    ILLUSTRATION_ATTEMPT_STATUSES,
    ILLUSTRATION_CONSISTENCY_VERDICTS,
    ILLUSTRATION_JOB_NONTERMINAL_STATUSES,
    ILLUSTRATION_JOB_STATUSES,
    ILLUSTRATION_RESERVATION_STATUSES,
    ILLUSTRATION_REVIEW_ACTIONS,
    ILLUSTRATION_RIGHTS_STATUSES,
    LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS,
    AssetRevisionContract,
    AssetRevisionView,
    ConsistencyReportContract,
    FrozenAssetRevisionView,
    IllustrationActorSource,
    IllustrationApprovalState,
    IllustrationConsistencyVerdict,
    IllustrationGateError,
    IllustrationJobContract,
    IllustrationJobView,
    IllustrationLineage,
    IllustrationRightsStatus,
    IllustrationReviewAction,
    IllustrationReviewEventInput,
    PriceSnapshot,
    approval_state_after,
    build_illustration_idempotency_key,
    canonical_illustration_hash,
    validate_asset_bytes,
    validate_asset_revision_contract,
    validate_illustration_job_contract,
    validate_illustration_review_event,
)
from app.services.illustrations.budget import (
    BudgetExceeded,
    IllustrationBudgetGate,
    IllustrationBudgetPolicy,
    UnknownPricing,
    worst_case_cost_usd,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64

SCENE_SPEC_HASH = "1" * 64
PROMPT_HASH = "2" * 64
VB_HASH = "3" * 64
SNAPSHOT_HASH = "4" * 64
CONFIG_HASH = "5" * 64
FIXTURE_HASH = "6" * 64

# Deterministic mock provider payload (illustration-mock-success fixture).
_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nmock-image-bytes-" + b"x" * 32

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"

ILLUSTRATION_TABLES = {
    "illustration_jobs",
    "illustration_attempts",
    "illustration_budget_ledgers",
    "illustration_budget_reservations",
    "asset_revisions",
    "illustration_consistency_reports",
    "illustration_review_events",
}

# Pinned canonical hashes of the closed vocabularies so a future rename cannot
# pass silently (stable hash pins the closed contract).
JOB_STATUSES_HASH = canonical_illustration_hash(
    {"job_statuses": list(ILLUSTRATION_JOB_STATUSES)}
)
ATTEMPT_STATUSES_HASH = canonical_illustration_hash(
    {"attempt_statuses": list(ILLUSTRATION_ATTEMPT_STATUSES)}
)
APPROVAL_STATES_HASH = canonical_illustration_hash(
    {"approval_states": list(ILLUSTRATION_APPROVAL_STATES)}
)
RIGHTS_STATUSES_HASH = canonical_illustration_hash(
    {"rights_statuses": list(ILLUSTRATION_RIGHTS_STATUSES)}
)
CONSISTENCY_VERDICTS_HASH = canonical_illustration_hash(
    {"consistency_verdicts": list(ILLUSTRATION_CONSISTENCY_VERDICTS)}
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _lineage(**overrides):
    payload = {
        "scene_spec_hash": SCENE_SPEC_HASH,
        "prompt_revision_id": 101,
        "prompt_revision_hash": PROMPT_HASH,
        "visual_bible_revision_id": None,
        "visual_bible_revision_hash": VB_HASH,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "cutoff_chapter": 8,
        "model_lineage": {
            "provider": "mock",
            "model": "mock-img-v1",
            "tier": "budget",
        },
        "config_hash": CONFIG_HASH,
    }
    payload.update(overrides)
    return IllustrationLineage.model_validate(payload)


def _job(**overrides):
    overrides = dict(overrides)
    lineage = overrides.pop("lineage", _lineage())
    payload = {
        "schema_version": "illustration.v1",
        "artifact_kind": "illustration_job",
        "owner_id": 11,
        "novel_id": 22,
        "job_key": "job-arin-court",
        "lineage": lineage,
        "price_snapshot": {"provider": "mock", "currency": "USD"},
        "idempotency_key": "0" * 64,
    }
    payload.update(overrides)
    job = IllustrationJobContract.model_validate(payload)
    if "idempotency_key" not in overrides:
        job = job.model_copy(
            update={
                "idempotency_key": build_illustration_idempotency_key(
                    job.owner_id, job.novel_id, job.lineage
                )
            }
        )
    return job


def _asset(**overrides):
    overrides = dict(overrides)
    lineage = overrides.pop("lineage", _lineage())
    payload_bytes = overrides.pop("payload", _IMAGE_BYTES)
    overrides.setdefault("bytes_hash", sha256(payload_bytes).hexdigest())
    overrides.setdefault("size_bytes", len(payload_bytes))
    payload = {
        "schema_version": "illustration-asset.v1",
        "artifact_kind": "illustration_asset",
        "owner_id": 11,
        "novel_id": 22,
        "job_id": 1,
        "revision_key": "rev-1",
        "revision_number": 1,
        "asset_id": "asset-1",
        "storage_key": f"assets/11/22/{HEX64}.png",
        "mime_type": "image/png",
        "width": 1024,
        "height": 1024,
        "size_bytes": len(payload_bytes),
        "bytes_hash": sha256(payload_bytes).hexdigest(),
        "lineage": lineage,
        "provider": "mock",
        "provider_model": "mock-img-v1",
        "provider_request_id": "req-1",
        "provider_response": {"redacted": True},
        "provenance": {"source": "mock-provider", "fixture": "illustration-mock-success"},
        "rights_status": "unreviewed",
        "approval_state": "candidate",
        "idempotency_key": HEX64,
    }
    payload.update(overrides)
    return AssetRevisionContract.model_validate(payload)


def _price_snapshot(**overrides):
    payload = {
        "provider": "mock",
        "model": "mock-img-v1",
        "currency": "USD",
        "input_price_per_million": Decimal("0.50"),
        "output_price_per_million": Decimal("1.50"),
        "image_price_per_image": Decimal("0.04"),
    }
    payload.update(overrides)
    return PriceSnapshot.model_validate(payload)


def _consistency(**overrides):
    payload = {
        "schema_version": "illustration-consistency.v1",
        "artifact_kind": "illustration_consistency_report",
        "owner_id": 11,
        "novel_id": 22,
        "asset_revision_id": 1,
        "report_key": "cons-1",
        "evaluator_id": "fixture-identity-v1",
        "evaluator_version": "1.0.0",
        "model_lineage": {"provider": "mock", "model": "consistency-fixture-v1"},
        "fixture_set_hash": FIXTURE_HASH,
        "reference_asset_ids": ["ref-char-arin-1", "ref-char-arin-2"],
        "scores": {"identity": 0.95, "style": 0.90},
        "verdict": "concern",
        "details": {"drift": ["unsupported_glasses"]},
        "idempotency_key": HEX64_B,
    }
    payload.update(overrides)
    return ConsistencyReportContract.model_validate(payload)


def _review_event(**overrides):
    payload = {
        "owner_id": 11,
        "novel_id": 22,
        "asset_revision_id": 1,
        "event_key": "approve-1",
        "action": "approve",
        "actor_source": "human",
        "actor": "editor",
        "reason": "matches the scene spec",
        "from_approval_state": "candidate",
    }
    payload.update(overrides)
    return IllustrationReviewEventInput.model_validate(payload)


def _frozen_asset_view(**overrides):
    payload = {
        "id": 1,
        "owner_id": 11,
        "novel_id": 22,
        "job_id": 1,
        "revision_key": "rev-1",
        "revision_number": 1,
        "asset_id": "asset-1",
        "storage_key": f"assets/11/22/{HEX64}.png",
        "mime_type": "image/png",
        "width": 1024,
        "height": 1024,
        "size_bytes": len(_IMAGE_BYTES),
        "bytes_hash": sha256(_IMAGE_BYTES).hexdigest(),
        "scene_spec_hash": SCENE_SPEC_HASH,
        "prompt_revision_hash": PROMPT_HASH,
        "visual_bible_revision_hash": VB_HASH,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "cutoff_chapter": 8,
        "provider": "mock",
        "provider_model": "mock-img-v1",
        "provider_request_id": "req-1",
        "rights_status": "cleared",
        "approval_state": "proposal_ready",
        "approved_by": "editor",
    }
    payload.update(overrides)
    return FrozenAssetRevisionView.model_validate(payload)


def _asset_view(**overrides):
    asset = _asset()
    payload = {
        "id": 1,
        "owner_id": asset.owner_id,
        "novel_id": asset.novel_id,
        "job_id": asset.job_id,
        "revision_key": asset.revision_key,
        "revision_number": asset.revision_number,
        "asset_id": asset.asset_id,
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
        "size_bytes": asset.size_bytes,
        "bytes_hash": asset.bytes_hash,
        "scene_spec_hash": asset.lineage.scene_spec_hash,
        "prompt_revision_id": asset.lineage.prompt_revision_id,
        "prompt_revision_hash": asset.lineage.prompt_revision_hash,
        "visual_bible_revision_hash": asset.lineage.visual_bible_revision_hash,
        "source_snapshot_id": asset.lineage.source_snapshot_id,
        "source_snapshot_hash": asset.lineage.source_snapshot_hash,
        "cutoff_chapter": asset.lineage.cutoff_chapter,
        "provider": asset.provider,
        "provider_model": asset.provider_model,
        "provider_request_id": asset.provider_request_id,
        "rights_status": asset.rights_status.value,
        "approval_state": asset.approval_state.value,
    }
    payload.update(overrides)
    return AssetRevisionView.model_validate(payload)


def _job_view(**overrides):
    job = _job()
    payload = {
        "id": 1,
        "owner_id": job.owner_id,
        "novel_id": job.novel_id,
        "job_key": job.job_key,
        "idempotency_key": job.idempotency_key,
        "status": "queued",
        "status_reason": None,
        "error_code": None,
        "retry_count": 0,
        "scene_spec_hash": job.lineage.scene_spec_hash,
        "prompt_revision_id": job.lineage.prompt_revision_id,
        "prompt_revision_hash": job.lineage.prompt_revision_hash,
        "visual_bible_revision_hash": job.lineage.visual_bible_revision_hash,
        "source_snapshot_id": job.lineage.source_snapshot_id,
        "source_snapshot_hash": job.lineage.source_snapshot_hash,
        "cutoff_chapter": job.lineage.cutoff_chapter,
        "config_hash": job.lineage.config_hash,
        "price_snapshot": job.price_snapshot,
    }
    payload.update(overrides)
    return IllustrationJobView.model_validate(payload)


# ---------------------------------------------------------------------------
# Vocabulary (closed and pinned)
# ---------------------------------------------------------------------------


def test_illustration_vocabulary_is_closed_and_pinned():
    assert [s.value for s in IllustrationApprovalState] == list(ILLUSTRATION_APPROVAL_STATES)
    assert [a.value for a in IllustrationReviewAction] == list(ILLUSTRATION_REVIEW_ACTIONS)
    assert [r.value for r in IllustrationRightsStatus] == list(ILLUSTRATION_RIGHTS_STATUSES)
    assert [v.value for v in IllustrationConsistencyVerdict] == list(
        ILLUSTRATION_CONSISTENCY_VERDICTS
    )
    assert JOB_STATUSES_HASH == "c90b3aa86bc17ed458792d47f824e21a37540f635b3b818be6fb1986f642e2ea"
    assert ATTEMPT_STATUSES_HASH == "8f68af445bf876c994c12292c006f3e4631ea4a7590b1853205f59816bddc314"
    assert APPROVAL_STATES_HASH == "8e0516cace67d20db5ae5ef36a2748f3e8e2cbecfa04adc64e91243b46714202"
    assert RIGHTS_STATUSES_HASH == "42a345ca5bdfdf6e40bd8c1489cbacbd98b052b5cc4ddd5219e6aa79611a2095"
    assert CONSISTENCY_VERDICTS_HASH == "b71e86befb4255abf29135f2077384da2b8f5538a3411c4f7ea912c70af8abbf"


def test_job_status_vocabulary_has_explicit_failure_and_unknown():
    statuses = set(ILLUSTRATION_JOB_STATUSES)
    assert {
        "queued",
        "running",
        "paused_budget",
        "paused_dependency",
        "succeeded",
        "failed",
        "cancelled",
        "outcome_unknown",
    } <= statuses
    assert set(ILLUSTRATION_JOB_NONTERMINAL_STATUSES) <= statuses


# ---------------------------------------------------------------------------
# Idempotent durable jobs (D-33-01)
# ---------------------------------------------------------------------------


def test_idempotency_key_replays_from_full_lineage():
    job_a = _job()
    job_b = _job()
    assert job_a.idempotency_key == job_b.idempotency_key
    assert job_a.idempotency_key == build_illustration_idempotency_key(
        job_a.owner_id, job_a.novel_id, job_a.lineage
    )
    assert len(job_a.idempotency_key) == 64
    validate_illustration_job_contract(job_a)
    validate_illustration_job_contract(job_b)


def test_idempotency_key_changes_when_lineage_changes():
    base = _job()
    assert _job(owner_id=12).idempotency_key != base.idempotency_key
    assert _job(novel_id=23).idempotency_key != base.idempotency_key
    assert (
        _job(lineage=_lineage(scene_spec_hash=HEX64_D)).idempotency_key
        != base.idempotency_key
    )
    assert (
        _job(lineage=_lineage(prompt_revision_hash=HEX64_D)).idempotency_key
        != base.idempotency_key
    )
    assert (
        _job(
            lineage=_lineage(
                model_lineage={"provider": "mock", "model": "mock-img-v2"}
            )
        ).idempotency_key
        != base.idempotency_key
    )
    assert (
        _job(lineage=_lineage(config_hash=HEX64_D)).idempotency_key
        != base.idempotency_key
    )


def test_non_replayable_idempotency_key_fails_closed():
    job = _job()
    bad = job.model_copy(update={"idempotency_key": "9" * 64})
    with pytest.raises(IllustrationGateError):
        validate_illustration_job_contract(bad)


def test_strict_job_contract_rejects_provider_fields_and_secrets():
    with pytest.raises(ValidationError):
        _job(prompt_text="A cinematic wide shot of Arin")
    with pytest.raises(ValidationError):
        _job(negative_prompt="no text")
    with pytest.raises(ValidationError):
        _job(provider_secret="sk-live")
    with pytest.raises(IllustrationGateError):
        validate_illustration_job_contract(
            _job(price_snapshot={"api_key": "sk-live"})
        )
    with pytest.raises(IllustrationGateError):
        validate_illustration_job_contract(
            _job(price_snapshot={"token": "secret-token"})
        )
    fields = set(IllustrationJobContract.model_fields)
    assert "cover_url" not in fields
    assert "active_pointer" not in fields
    assert "promote_to_canon" not in fields


# ---------------------------------------------------------------------------
# Immutable asset revisions (D-33-03)
# ---------------------------------------------------------------------------


def test_asset_bytes_replay_and_no_empty_success():
    asset = _asset()
    validate_asset_bytes(asset, _IMAGE_BYTES)
    # An empty provider payload can never become a successful asset revision.
    with pytest.raises(IllustrationGateError):
        validate_asset_bytes(asset, b"")
    # A mutated payload is detected by hash and size.
    with pytest.raises(IllustrationGateError):
        validate_asset_bytes(asset, _IMAGE_BYTES + b"tampered")


def test_asset_is_immutable_candidate_with_metadata():
    asset = _asset()
    assert asset.approval_state is IllustrationApprovalState.CANDIDATE
    assert asset.rights_status is IllustrationRightsStatus.UNREVIEWED
    assert asset.mime_type == "image/png"
    assert asset.width == 1024 and asset.height == 1024
    with pytest.raises(ValidationError):
        _asset(cover_url="http://example.com/cover.jpg")
    with pytest.raises(ValidationError):
        _asset(approved=True)
    fields = set(AssetRevisionContract.model_fields)
    assert "canon" not in fields
    assert "active_pointer" not in fields
    assert "cover_url" not in fields


def test_asset_revision_lineage_must_match_job():
    job = _job()
    asset = _asset(job_id=1)
    validate_asset_revision_contract(asset, job)

    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(
            _asset(owner_id=12, job_id=1), job
        )
    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(_asset(novel_id=23, job_id=1), job)
    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(
            _asset(lineage=_lineage(scene_spec_hash=HEX64_D), job_id=1), job
        )
    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(
            _asset(lineage=_lineage(prompt_revision_hash=HEX64_D), job_id=1), job
        )
    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(
            _asset(lineage=_lineage(visual_bible_revision_hash=HEX64_D), job_id=1), job
        )
    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(
            _asset(lineage=_lineage(source_snapshot_hash=HEX64_D), job_id=1), job
        )
    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(
            _asset(lineage=_lineage(cutoff_chapter=9), job_id=1), job
        )
    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(
            _asset(lineage=_lineage(config_hash=HEX64_D), job_id=1), job
        )
    # A new revision must enter as candidate; an already-approved asset cannot
    # be created out of thin air.
    with pytest.raises(IllustrationGateError):
        validate_asset_revision_contract(
            _asset(approval_state="proposal_ready", job_id=1), job
        )


def test_asset_revision_has_no_promotion_path():
    with pytest.raises(ValidationError):
        _asset(promote_to_canon=True)
    assert _asset().approval_state is IllustrationApprovalState.CANDIDATE


# ---------------------------------------------------------------------------
# Budget and cost (D-33-02)
# ---------------------------------------------------------------------------


def test_worst_case_cost_from_price_snapshot():
    price = _price_snapshot()
    assert worst_case_cost_usd(
        price, calls=1, input_tokens=0, output_tokens=0
    ) == Decimal("0.04")
    # 0.04 + (0.5*1000 + 1.5*2000)/1e6 = 0.04 + 0.0005 + 0.003 = 0.0435
    assert worst_case_cost_usd(
        price, calls=1, input_tokens=1000, output_tokens=2000
    ) == Decimal("0.0435")


def test_unknown_pricing_fails_closed():
    no_image = _price_snapshot(image_price_per_image=None)
    with pytest.raises(UnknownPricing):
        worst_case_cost_usd(no_image, calls=1, input_tokens=0, output_tokens=0)
    no_input = _price_snapshot(input_price_per_million=None)
    with pytest.raises(UnknownPricing):
        worst_case_cost_usd(no_input, calls=0, input_tokens=100, output_tokens=0)
    # A snapshot with no price at all is rejected at contract level.
    with pytest.raises(ValidationError):
        _price_snapshot(
            input_price_per_million=None,
            output_price_per_million=None,
            image_price_per_image=None,
        )


def test_budget_reserve_is_idempotent_and_settles_explicit():
    gate = IllustrationBudgetGate(
        policy=IllustrationBudgetPolicy(
            max_calls=5, max_cost_usd=Decimal("0.50")
        )
    )
    price = _price_snapshot()
    first = gate.reserve(
        "job:1:attempt:1",
        calls=1,
        input_tokens=1000,
        output_tokens=2000,
        price_snapshot=price,
    )
    duplicate = gate.reserve(
        "job:1:attempt:1",
        calls=1,
        input_tokens=1000,
        output_tokens=2000,
        price_snapshot=price,
    )
    assert duplicate is first  # one reservation, one charge for a duplicate
    gate.settle(
        "job:1:attempt:1",
        actual_input_tokens=1000,
        actual_output_tokens=2000,
        actual_cost_usd=Decimal("0.0435"),
    )
    assert first.status == "settled"
    assert first.settled_usage["usage_unknown"] is False
    assert gate.settled_calls == 1


def test_budget_exhaustion_fails_closed():
    gate = IllustrationBudgetGate(
        policy=IllustrationBudgetPolicy(
            max_calls=1, max_cost_usd=Decimal("0.10")
        )
    )
    price = _price_snapshot()
    gate.reserve(
        "job:1:attempt:1",
        calls=1,
        input_tokens=0,
        output_tokens=0,
        price_snapshot=price,
    )
    with pytest.raises(BudgetExceeded):
        gate.reserve(
            "job:2:attempt:1",
            calls=1,
            input_tokens=0,
            output_tokens=0,
            price_snapshot=price,
        )
    assert gate.network_calls_allowed is False


def test_unknown_usage_cost_stays_explicit():
    gate = IllustrationBudgetGate()
    price = _price_snapshot()
    gate.reserve(
        "job:1:attempt:1",
        calls=1,
        input_tokens=0,
        output_tokens=0,
        price_snapshot=price,
    )
    gate.settle_unknown("job:1:attempt:1", error_code="provider_timeout")
    res = gate.reservations["job:1:attempt:1"]
    assert res.settled_usage["usage_unknown"] is True
    assert res.settled_usage["cost_usd"] is None
    assert res.settled_usage["error_code"] == "provider_timeout"
    assert gate.snapshot()["settled_unknown_count"] == 1


# ---------------------------------------------------------------------------
# Consistency reports are evidence, not canon (D-33-04)
# ---------------------------------------------------------------------------


def test_consistency_report_keeps_fixture_model_lineage():
    report = _consistency()
    assert report.verdict is IllustrationConsistencyVerdict.CONCERN
    assert report.fixture_set_hash == FIXTURE_HASH
    assert report.reference_asset_ids == ["ref-char-arin-1", "ref-char-arin-2"]
    assert report.scores["identity"] == 0.95
    # A report has no promotion surface and cannot touch the Visual Bible.
    with pytest.raises(ValidationError):
        _consistency(rewrite_visual_bible=True)
    fields = set(ConsistencyReportContract.model_fields)
    assert "canon" not in fields
    assert "auto_approve" not in fields


def test_consistency_verdict_is_closed():
    with pytest.raises(ValidationError):
        _consistency(verdict="perfect")
    assert {v.value for v in IllustrationConsistencyVerdict} == set(
        ILLUSTRATION_CONSISTENCY_VERDICTS
    )


# ---------------------------------------------------------------------------
# Candidate-only approval gate (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------


def test_approval_transition_map_is_closed():
    assert set(LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS) == set(IllustrationApprovalState)
    for state, actions in LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS.items():
        for action in actions:
            assert action in ILLUSTRATION_ACTION_TO_STATE
            assert approval_state_after(state, action) == ILLUSTRATION_ACTION_TO_STATE[action]


def test_approval_chain_and_idempotency():
    assert approval_state_after("candidate", "approve") is IllustrationApprovalState.PROPOSAL_READY
    assert approval_state_after("candidate", "reject") is IllustrationApprovalState.REJECTED
    assert approval_state_after("candidate", "needs_relink") is IllustrationApprovalState.CANDIDATE
    assert approval_state_after("proposal_ready", "supersede") is IllustrationApprovalState.SUPERSEDED
    assert approval_state_after("proposal_ready", "needs_relink") is IllustrationApprovalState.CANDIDATE
    with pytest.raises(IllustrationGateError):
        approval_state_after("proposal_ready", "approve")  # double approval impossible
    with pytest.raises(IllustrationGateError):
        approval_state_after("superseded", "approve")  # terminal

    result = validate_illustration_review_event(_review_event())
    assert result is IllustrationApprovalState.PROPOSAL_READY
    with pytest.raises(IllustrationGateError):
        validate_illustration_review_event(
            _review_event(), seen_event_keys={"approve-1"}
        )


def test_frozen_asset_revision_requires_proposal_ready_and_cleared_rights():
    with pytest.raises(ValidationError):
        _frozen_asset_view(approval_state="candidate")
    with pytest.raises(ValidationError):
        _frozen_asset_view(approval_state="rejected")
    with pytest.raises(ValidationError):
        _frozen_asset_view(rights_status="unreviewed")
    frozen = _frozen_asset_view()
    assert frozen.approval_state is IllustrationApprovalState.PROPOSAL_READY
    assert frozen.rights_status is IllustrationRightsStatus.CLEARED


def test_read_envelopes_load():
    view = _asset_view()
    assert view.approval_state is IllustrationApprovalState.CANDIDATE
    assert view.bytes_hash == sha256(_IMAGE_BYTES).hexdigest()

    job_view = _job_view()
    assert job_view.status.value == "queued"
    assert job_view.prompt_revision_hash == PROMPT_HASH
    assert job_view.idempotency_key == _job().idempotency_key


# ---------------------------------------------------------------------------
# ORM metadata, append-only content rows and migration chain
# ---------------------------------------------------------------------------


def test_illustration_tables_are_registered_on_metadata():
    tables = set(AssetRevision.metadata.tables)
    assert ILLUSTRATION_TABLES <= tables


def test_orm_exports_all_illustration_entities():
    from app.models import (
        AssetRevision as ExportedAsset,
        ConsistencyReport as ExportedReport,
        IllustrationAttempt as ExportedAttempt,
        IllustrationBudgetLedger as ExportedLedger,
        IllustrationBudgetReservation as ExportedReservation,
        IllustrationJob as ExportedJob,
        IllustrationReviewEvent as ExportedReview,
    )

    assert ExportedJob.__tablename__ == "illustration_jobs"
    assert ExportedAttempt.__tablename__ == "illustration_attempts"
    assert ExportedLedger.__tablename__ == "illustration_budget_ledgers"
    assert ExportedReservation.__tablename__ == "illustration_budget_reservations"
    assert ExportedAsset.__tablename__ == "asset_revisions"
    assert ExportedReport.__tablename__ == "illustration_consistency_reports"
    assert ExportedReview.__tablename__ == "illustration_review_events"


def test_job_orm_carries_lineage_and_nonterminal_idempotency():
    cols = set(inspect(IllustrationJob).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "job_key",
        "idempotency_key",
        "status",
        "lease_id",
        "lease_expires_at",
        "cancel_requested",
        "retry_count",
        "scene_spec_hash",
        "prompt_revision_id",
        "prompt_revision_hash",
        "visual_bible_revision_hash",
        "source_snapshot_id",
        "source_snapshot_hash",
        "cutoff_chapter",
        "model_lineage",
        "config_hash",
        "price_snapshot",
        "error_code",
    } <= cols

    unique = {
        tuple(c.name for c in u.columns)
        for u in IllustrationJob.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "id") in unique

    index_names = {i.name for i in IllustrationJob.__table__.indexes}
    assert "uq_illustration_jobs_nonterminal_key" in index_names
    assert "idx_illustration_jobs_scope" in index_names

    check_names = {
        c.name
        for c in IllustrationJob.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_illustration_jobs_status" in check_names
    assert "ck_illustration_jobs_idempotency_key" in check_names


def test_attempt_orm_enforces_job_number_uniqueness():
    unique = {
        tuple(c.name for c in u.columns)
        for u in IllustrationAttempt.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("job_id", "attempt_number") in unique
    check_names = {
        c.name
        for c in IllustrationAttempt.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_illustration_attempts_status" in check_names
    assert "ck_illustration_attempts_number" in check_names


def test_asset_orm_carries_immutable_lineage_and_approval_projection():
    cols = set(inspect(AssetRevision).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "job_id",
        "revision_key",
        "revision_number",
        "asset_id",
        "storage_key",
        "mime_type",
        "width",
        "height",
        "size_bytes",
        "bytes_hash",
        "scene_spec_hash",
        "prompt_revision_hash",
        "visual_bible_revision_hash",
        "source_snapshot_id",
        "source_snapshot_hash",
        "cutoff_chapter",
        "model_lineage",
        "config_hash",
        "provider_request_id",
        "provenance",
        "rights_status",
        "approval_state",
        "approved_by",
    } <= cols

    check_names = {
        c.name
        for c in AssetRevision.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_asset_revisions_approval_state" in check_names
    assert "ck_asset_revisions_bytes_hash" in check_names
    assert "ck_asset_revisions_dimensions" in check_names

    unique = {
        tuple(c.name for c in u.columns)
        for u in AssetRevision.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "job_id", "revision_key") in unique
    assert ("idempotency_key",) in unique


def test_consistency_orm_enforces_closed_verdict():
    check_names = {
        c.name
        for c in ConsistencyReport.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_illustration_consistency_verdict" in check_names
    assert "ck_illustration_consistency_fixture_hash" in check_names


def test_budget_orm_enforces_owner_scope_and_reservation_key():
    unique = {
        tuple(c.name for c in u.columns)
        for u in IllustrationBudgetLedger.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id") in unique
    res_unique = {
        tuple(c.name for c in u.columns)
        for u in IllustrationBudgetReservation.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("ledger_id", "reservation_key") in res_unique


async def _user_and_novel(db_session: AsyncSession, username: str):
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()
    novel = Novel(title=f"Illustration Novel {username}", owner_id=user.id)
    db_session.add(novel)
    await db_session.flush()
    return user, novel


async def _persist_job(
    db_session: AsyncSession, username: str, *, status: str = "queued"
) -> tuple[IllustrationJob, User, Novel]:
    owner, novel = await _user_and_novel(db_session, username)
    row = IllustrationJob(
        owner_id=owner.id,
        novel_id=novel.id,
        job_key="job-append",
        idempotency_key=HEX64,
        status=status,
        status_reason=None,
        error_code=None,
        lease_id=None,
        lease_expires_at=None,
        heartbeat_at=None,
        cancel_requested=False,
        retry_count=0,
        scene_spec_hash=SCENE_SPEC_HASH,
        prompt_revision_id=101,
        prompt_revision_hash=PROMPT_HASH,
        visual_bible_revision_id=None,
        visual_bible_revision_hash=VB_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        cutoff_chapter=8,
        model_lineage={},
        config_hash=CONFIG_HASH,
        price_snapshot={},
        response_hash=None,
        schema_version="illustration.v1",
    )
    db_session.add(row)
    await db_session.flush()
    return row, owner, novel


async def _persist_asset(
    db_session: AsyncSession, username: str
) -> tuple[AssetRevision, IllustrationJob, User, Novel]:
    job_row, owner, novel = await _persist_job(db_session, username)
    asset_row = AssetRevision(
        owner_id=owner.id,
        novel_id=novel.id,
        job_id=job_row.id,
        revision_key="rev-1",
        revision_number=1,
        asset_id="asset-1",
        storage_key=f"assets/{owner.id}/{novel.id}/{HEX64}.png",
        mime_type="image/png",
        width=1024,
        height=1024,
        size_bytes=len(_IMAGE_BYTES),
        bytes_hash=sha256(_IMAGE_BYTES).hexdigest(),
        scene_spec_hash=SCENE_SPEC_HASH,
        prompt_revision_id=101,
        prompt_revision_hash=PROMPT_HASH,
        visual_bible_revision_hash=VB_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        cutoff_chapter=8,
        model_lineage={},
        config_hash=CONFIG_HASH,
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
        idempotency_key=HEX64_B,
        projection_hash=HEX64,
        schema_version="illustration-asset.v1",
    )
    db_session.add(asset_row)
    await db_session.flush()
    return asset_row, job_row, owner, novel


async def test_asset_content_is_immutable(db_session: AsyncSession):
    asset_row, _, _, _ = await _persist_asset(db_session, "ill_append_asset")
    asset_row.mime_type = "image/jpeg"
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_asset_approval_projection_is_the_only_mutable_surface(
    db_session: AsyncSession,
):
    asset_row, _, _, _ = await _persist_asset(db_session, "ill_approve_asset")
    asset_row.approval_state = "proposal_ready"
    asset_row.approved_by = "editor"
    await db_session.flush()
    assert asset_row.approval_state == "proposal_ready"
    assert asset_row.approved_by == "editor"
    # The immutable content still fails closed after the projection moved.
    asset_row.bytes_hash = "0" * 64
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_review_event_row_is_append_only(db_session: AsyncSession):
    asset_row, _, owner, novel = await _persist_asset(
        db_session, "ill_append_review"
    )
    event = IllustrationReviewEvent(
        owner_id=owner.id,
        novel_id=novel.id,
        asset_revision_id=asset_row.id,
        action="approve",
        actor_source="human",
        actor="editor",
        reason="matches the scene spec",
        event_key="approve-1",
        from_approval_state="candidate",
        to_approval_state="proposal_ready",
        details={},
    )
    db_session.add(event)
    await db_session.flush()
    event.reason = "mutated"
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_attempt_unique_job_number(db_session: AsyncSession):
    job_row, _, _ = await _persist_job(db_session, "ill_attempt_uniq")
    first = IllustrationAttempt(
        job_id=job_row.id,
        attempt_number=1,
        status="started",
        provider_request_id=None,
        request_hash=HEX64,
        response_hash=None,
        usage={},
        cost_usd=None,
        latency_ms=None,
        error_code=None,
    )
    db_session.add(first)
    await db_session.flush()
    second = IllustrationAttempt(
        job_id=job_row.id,
        attempt_number=1,
        status="started",
        provider_request_id=None,
        request_hash=HEX64,
        response_hash=None,
        usage={},
        cost_usd=None,
        latency_ms=None,
        error_code=None,
    )
    db_session.add(second)
    with pytest.raises(Exception):
        await db_session.flush()


def _load_migration(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_is_serial_on_top_of_prompt_review_events_head():
    migration = _load_migration("20260801_illustration_jobs.py")
    assert migration.revision == "20260801_illustration_jobs"
    assert migration.down_revision == "20260801_prompt_review_events"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)
    assert "asset_revisions" in migration.__doc__
    assert "illustration_jobs" in migration.__doc__
    assert "'outcome_unknown'" in migration._JOB_STATUSES
    assert "'proposal_ready'" in migration._APPROVAL_STATES
    assert "'unavailable'" in migration._CONSISTENCY_VERDICTS


def test_migration_matches_orm_table_set():
    migration = _load_migration("20260801_illustration_jobs.py")
    for table in ILLUSTRATION_TABLES:
        assert table in migration.__doc__


def test_no_cover_or_publish_crossing_in_contracts():
    """Illustration contracts never reuse cover_url and have no publish/active
    pointer (D-33-03); Phase 33 ends at proposal_ready."""
    for contract in (IllustrationJobContract, AssetRevisionContract):
        fields = set(contract.model_fields)
        assert "cover_url" not in fields
        assert "active_pointer" not in fields
        assert "current_revision" not in fields
        assert "canon_url" not in fields
        assert "promote_to_canon" not in fields
        assert "published" not in fields
