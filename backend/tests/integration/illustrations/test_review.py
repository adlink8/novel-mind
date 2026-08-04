"""Phase 33-04 illustration review workflow integration tests (REQ-VIS-04).

Covers the 33-VALIDATION ``illustration-review`` fixture and the PLAN acceptance
gates:
- proposal_ready requires a succeeded job, complete source/prompt/model/asset
  lineage, cleared rights, settled durable budget evidence and a visible
  consistency report; any missing piece fails closed with a stable reason code;
- explicit approve/reject/supersede/needs_relink actions are append-only and
  idempotent (a repeated event_key replays without a second event);
- approval only moves the candidate's projection to proposal_ready — no
  published transition exists and nothing becomes reader/export visible
  (Phase 34 consumes FrozenAssetRevisionView);
- the gallery and the full review envelope expose job/attempt/budget/consistency
  evidence and the append-only review history;
- all reads/writes are owner-scoped (cross-owner is 404-equivalent).

Fixtures: the SQLite in-memory ``db_session`` / ``auth_client`` from the
top-level conftest; the durable worker runs against the same engine through
``TestSessionLocal``. No external service is required.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.illustrations import (
    IllustrationGenerationRequest,
    set_illustration_asset_storage,
    set_illustration_consistency_fixtures,
)
from app.models import Novel, User
from app.models.illustration import AssetRevision, ConsistencyReport
from app.models.illustration_job import (
    IllustrationBudgetLedger,
    IllustrationBudgetReservation,
    IllustrationJob,
    IllustrationReviewEvent,
)
from app.models.prompt_revision import PromptRevision
from app.models.scene_spec import SceneSpecVersion
from app.models.visual_bible import VisualBibleVersion
from app.schemas.illustration import (
    IllustrationApprovalState,
    IllustrationReviewAction,
)
from app.services.illustrations.consistency import (
    CandidateConsistencyEvidence,
    ConsistencyEvaluator,
    ConsistencyReportService,
    mock_consistency_fixture_registry,
)
from app.services.illustrations.gateway import (
    IllustrationGateway,
    MockIllustrationTransport,
)
from app.services.illustrations.review import (
    IllustrationProposalGateResult,
    IllustrationReviewGateError,
    IllustrationReviewNotFound,
    IllustrationReviewService,
    build_gallery,
    build_proposal_ref,
    build_review_envelope,
    evaluate_illustration_proposal_gate,
)
from app.services.illustrations.storage import AssetStorage
from app.services.illustrations.worker import (
    IllustrationWorkerRuntime,
    run_illustration_worker,
)
from app.services.key_scenes.boundaries import SceneBoundaryService
from tests.conftest import TestSessionLocal

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
VB_HASH = "1" * 64
SCENE_SPEC_HASH = "3" * 64
SNAPSHOT_HASH = "4" * 64
PROMPT_HASH = "5" * 64
INPUT_HASH = "7" * 64

_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nmock-review-image-" + b"x" * 32


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Chain + row seeding helpers
# ---------------------------------------------------------------------------


async def _source_snapshot_hash(db: AsyncSession, owner_id: int, novel_id: int) -> str:
    snapshot_hash, _ = await SceneBoundaryService(db).load_source_snapshot(
        owner_id=owner_id, novel_id=novel_id
    )
    return snapshot_hash


async def _get_testuser(db: AsyncSession) -> User:
    user = await db.scalar(select(User).where(User.username == "testuser"))
    if user is None:
        user = User(
            username="testuser", email="test@example.com", hashed_password="hash"
        )
        db.add(user)
        await db.flush()
    return user


async def _seed_testuser_novel(db: AsyncSession) -> tuple[User, Novel]:
    user = await _get_testuser(db)
    novel = Novel(title="Review Novel testuser", owner_id=user.id)
    db.add(novel)
    await db.flush()
    return user, novel


async def _seed_user_and_novel(db: AsyncSession, username: str) -> tuple[User, Novel]:
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db.add(user)
    await db.flush()
    novel = Novel(title=f"Review Novel {username}", owner_id=user.id)
    db.add(novel)
    await db.flush()
    return user, novel


async def _demote_testuser(db: AsyncSession) -> None:
    user = await _get_testuser(db)
    if user.is_superuser:
        user.is_superuser = False
        await db.flush()


async def _make_prompt_chain(
    db: AsyncSession,
    user: User,
    novel: Novel,
    *,
    approved: bool = True,
    vb_hash: str = VB_HASH,
    snapshot_hash: str | None = None,
    prompt_hash: str = PROMPT_HASH,
):
    snapshot_hash = snapshot_hash or await _source_snapshot_hash(
        db, user.id, novel.id
    )
    vb = VisualBibleVersion(
        owner_id=user.id,
        novel_id=novel.id,
        version_key=f"vb-{vb_hash[:12]}",
        revision_number=1,
        source_snapshot_id="ss-1",
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=8,
        review_state="approved",
        schema_version="visual-bible.v1",
        schema_hash=HEX64,
        policy_hash=HEX64,
        manifest_hash=vb_hash,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=_h(f"vb-{vb_hash}"),
        projection_hash=HEX64,
    )
    db.add(vb)
    await db.flush()

    spec = SceneSpecVersion(
        owner_id=user.id,
        novel_id=novel.id,
        spec_key=f"spec-{vb_hash[:12]}",
        revision_number=1,
        scene_candidate_id=None,
        scene_candidate_hash=HEX64,
        visual_bible_revision_id=vb.id,
        visual_bible_revision_hash=vb_hash,
        source_snapshot_id="ss-1",
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=8,
        review_state="approved",
        schema_version="scene-spec.v1",
        schema_hash=HEX64,
        compiler_id="mock-compiler",
        compiler_version="1.0.0",
        policy_hash=HEX64,
        content_hash=SCENE_SPEC_HASH,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=_h(f"spec-{vb_hash}"),
        projection_hash=HEX64,
    )
    db.add(spec)
    await db.flush()

    prompt = PromptRevision(
        owner_id=user.id,
        novel_id=novel.id,
        prompt_key=f"prompt-{vb_hash[:12]}",
        revision_number=1,
        scene_spec_id=spec.id,
        scene_spec_hash=SCENE_SPEC_HASH,
        visual_bible_revision_id=vb.id,
        visual_bible_revision_hash=vb_hash,
        source_snapshot_id="ss-1",
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=8,
        review_state="approved" if approved else "candidate",
        schema_version="prompt-revision.v1",
        schema_hash=HEX64,
        prompt_schema_hash=HEX64,
        compiler_version="1.0.0",
        adapter_id="mock-provider",
        adapter_version="1.0.0",
        config_hash=HEX64,
        input_hash=INPUT_HASH,
        prompt_hash=prompt_hash,
        sections={},
        negative_constraints=[],
        uncertainties=[],
        prompt_text="A cinematic wide shot of Arin in the bamboo forest.",
        redacted_preview=None,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=_h(f"prompt-{vb_hash}-{prompt_hash}"),
        projection_hash=HEX64,
    )
    db.add(prompt)
    await db.flush()
    return prompt, vb, spec, snapshot_hash


def _seed_asset_row(
    *,
    user: User,
    novel: Novel,
    job: IllustrationJob,
    rights_status: str = "cleared",
    approval_state: str = "candidate",
    scene_spec_hash: str = SCENE_SPEC_HASH,
    revision_key: str = "rev-1",
    revision_number: int = 1,
) -> AssetRevision:
    """Create an immutable candidate asset row bound to a seeded job."""
    return AssetRevision(
        owner_id=user.id,
        novel_id=novel.id,
        job_id=job.id,
        revision_key=revision_key,
        revision_number=revision_number,
        asset_id=f"asset-{user.id}-{novel.id}-{revision_number}",
        storage_key=f"assets/{user.id}/{novel.id}/{HEX64}.png",
        mime_type="image/png",
        width=1024,
        height=1024,
        size_bytes=len(_IMAGE_BYTES),
        bytes_hash=hashlib.sha256(_IMAGE_BYTES).hexdigest(),
        scene_spec_hash=scene_spec_hash,
        prompt_revision_id=job.prompt_revision_id,
        prompt_revision_hash=job.prompt_revision_hash,
        visual_bible_revision_hash=job.visual_bible_revision_hash,
        source_snapshot_id=job.source_snapshot_id,
        source_snapshot_hash=job.source_snapshot_hash,
        cutoff_chapter=job.cutoff_chapter,
        model_lineage=dict(job.model_lineage or {}),
        config_hash=job.config_hash,
        provider="mock",
        provider_model="mock-img-v1",
        provider_request_id="req-review-1",
        provider_response={},
        provenance={"source": "mock", "fixture": "illustration-mock-success"},
        rights_status=rights_status,
        approval_state=approval_state,
        approved_by=None,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=job.idempotency_key,
        projection_hash=HEX64,
        schema_version="illustration-asset.v1",
    )


def _seed_job(
    *,
    user: User,
    novel: Novel,
    status: str = "succeeded",
    job_key: str = "job-review",
) -> IllustrationJob:
    return IllustrationJob(
        owner_id=user.id,
        novel_id=novel.id,
        job_key=job_key,
        # Unique per seeded job: the AssetRevision idempotency_key is globally
        # unique, so every seeded job must carry a distinct 64-hex key.
        idempotency_key=_h(f"job-{user.id}-{novel.id}-{job_key}"),
        status=status,
        status_reason="generated" if status == "succeeded" else status,
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
        model_lineage={"provider": "mock", "model": "mock-img-v1"},
        config_hash=HEX64,
        price_snapshot={"provider": "mock", "model": "mock-img-v1"},
        response_hash=None,
        schema_version="illustration.v1",
    )


async def _persist_settled_budget(
    db: AsyncSession, job: IllustrationJob, user: User, novel: Novel
) -> IllustrationBudgetReservation:
    ledger = await db.scalar(
        select(IllustrationBudgetLedger).where(
            IllustrationBudgetLedger.owner_id == user.id,
            IllustrationBudgetLedger.novel_id == novel.id,
        )
    )
    if ledger is None:
        ledger = IllustrationBudgetLedger(
            owner_id=user.id,
            novel_id=novel.id,
            max_calls=10,
            max_cost_usd=Decimal("1.00"),
        )
        db.add(ledger)
        await db.flush()
    reservation = IllustrationBudgetReservation(
        ledger_id=ledger.id,
        reservation_key=f"job:{job.id}:attempt:1",
        status="settled",
        calls=1,
        input_tokens=120,
        output_tokens=1024,
        cost_usd=Decimal("0.04"),
        price_snapshot={"provider": "mock", "model": "mock-img-v1"},
        settled_usage={
            "input_tokens": 120,
            "output_tokens": 1024,
            "cost_usd": "0.04",
            "usage_unknown": False,
        },
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def _persist_consistency_report(
    db: AsyncSession, user: User, novel: Novel, asset: AssetRevision
) -> ConsistencyReport:
    evaluator = ConsistencyEvaluator(mock_consistency_fixture_registry())
    service = ConsistencyReportService(db, evaluator=evaluator)
    report, _replayed = await service.evaluate(
        owner_id=user.id,
        novel_id=novel.id,
        asset_revision_id=asset.id,
        report_key="arin:ch1",
        evidence=CandidateConsistencyEvidence(
            character_key="arin",
            scene_key="ch1",
            identity_attributes=(
                "black_hair",
                "amber_eyes",
                "lean_build",
                "scar_left_brow",
            ),
            style_attributes=("ink_painting", "warm_palette", "soft_lighting"),
            negative_constraints_present=(),
        ),
    )
    return report


async def _seed_complete_candidate(
    db: AsyncSession,
    user: User,
    novel: Novel,
    *,
    job_status: str = "succeeded",
    rights_status: str = "cleared",
    with_budget: bool = True,
    with_report: bool = True,
    seed_id: str = "job-review",
) -> AssetRevision:
    """Seed a candidate that satisfies the proposal gate by default.

    Individual preconditions can be dropped to prove each fail-closed reason.
    """
    job = _seed_job(user=user, novel=novel, status=job_status, job_key=seed_id)
    db.add(job)
    await db.flush()
    asset = _seed_asset_row(user=user, novel=novel, job=job, rights_status=rights_status)
    db.add(asset)
    await db.flush()
    if with_budget:
        await _persist_settled_budget(db, job, user, novel)
    if with_report:
        await _persist_consistency_report(db, user, novel, asset)
    await db.flush()
    return asset


def _generation_request() -> IllustrationGenerationRequest:
    return IllustrationGenerationRequest.model_validate(
        {
            "prompt_revision_id": 1,
            "job_key": "job-review-arin",
            "provider": "mock",
            "model": "mock-img-v1",
            "width": 1024,
            "height": 1024,
        }
    )


def _runtime(tmp_path):
    storage = AssetStorage(tmp_path / "assets")
    transport = MockIllustrationTransport(mode="success")
    runtime = IllustrationWorkerRuntime(
        sessions=TestSessionLocal,
        gateway=IllustrationGateway(transport),
        storage=storage,
    )
    return runtime, transport, storage


def _review_url(novel_id: int, asset_id: int) -> str:
    return f"/api/novels/{novel_id}/illustrations/assets/{asset_id}/review"


def _approve_payload(asset_id: int, *, event_key: str | None = None, **overrides) -> dict:
    payload = {
        "event_key": event_key or f"ev-{asset_id}-approve",
        "action": "approve",
        "actor_source": "human",
        "actor": "owner",
        "reason": "人工审查：批准",
        "from_approval_state": "candidate",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def no_illustration_dispatch():
    from app.main import app as fastapi_app

    fastapi_app.state.illustration_dispatch_enabled = False
    yield
    fastapi_app.state.illustration_dispatch_enabled = True


@pytest_asyncio.fixture
def illustration_storage(tmp_path) -> AssetStorage:
    storage = AssetStorage(tmp_path / "assets")
    set_illustration_asset_storage(storage)
    yield storage
    set_illustration_asset_storage(None)


@pytest_asyncio.fixture
async def consistency_fixtures_enabled():
    set_illustration_consistency_fixtures(mock_consistency_fixture_registry())
    yield
    set_illustration_consistency_fixtures({})


# ---------------------------------------------------------------------------
# Pure proposal gate (lineage completeness is constraint-free here)
# ---------------------------------------------------------------------------


def test_proposal_gate_pure_lineage_and_preconditions():
    ok_lineage = {
        "scene_spec_hash": "3" * 64,
        "prompt_revision_hash": "5" * 64,
        "visual_bible_revision_hash": "1" * 64,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": "4" * 64,
        "cutoff_chapter": 8,
        "config_hash": "6" * 64,
    }
    assert evaluate_illustration_proposal_gate(
        job_status="succeeded",
        rights_status="cleared",
        budget_settled=True,
        has_consistency_report=True,
        lineage=ok_lineage,
    ) == IllustrationProposalGateResult(ok=True)

    assert evaluate_illustration_proposal_gate(
        job_status="failed",
        rights_status="cleared",
        budget_settled=True,
        has_consistency_report=True,
        lineage=ok_lineage,
    ).reason_code == "job_not_succeeded"

    assert evaluate_illustration_proposal_gate(
        job_status="succeeded",
        rights_status="unreviewed",
        budget_settled=True,
        has_consistency_report=True,
        lineage=ok_lineage,
    ).reason_code == "rights_unresolved"

    assert evaluate_illustration_proposal_gate(
        job_status="succeeded",
        rights_status="cleared",
        budget_settled=False,
        has_consistency_report=True,
        lineage=ok_lineage,
    ).reason_code == "budget_unsettled"

    assert evaluate_illustration_proposal_gate(
        job_status="succeeded",
        rights_status="cleared",
        budget_settled=True,
        has_consistency_report=False,
        lineage=ok_lineage,
    ).reason_code == "consistency_missing"

    broken = dict(ok_lineage)
    broken["prompt_revision_hash"] = "short"
    broken.pop("cutoff_chapter")
    broken["source_snapshot_id"] = "  "
    result = evaluate_illustration_proposal_gate(
        job_status="succeeded",
        rights_status="cleared",
        budget_settled=True,
        has_consistency_report=True,
        lineage=broken,
    )
    assert result.reason_code == "lineage_incomplete"
    assert "prompt_revision_hash" in result.detail
    assert "source_snapshot_id" in result.detail
    assert "cutoff_chapter" in result.detail


def test_proposal_gate_has_no_publish_transition():
    # The review vocabulary has no publish action and no published state: the
    # only way out of proposal_ready is reject/supersede/needs_relink.
    assert "publish" not in {action.value for action in IllustrationReviewAction}
    assert "published" not in {state.value for state in IllustrationApprovalState}
    from app.schemas.illustration import LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS

    assert IllustrationApprovalState.PROPOSAL_READY.value == "proposal_ready"
    assert (
        IllustrationReviewAction.APPROVE.value
        in LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS[
            IllustrationApprovalState.CANDIDATE
        ]
    )
    assert "publish" not in {
        action.value
        for transitions in LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS.values()
        for action in transitions
    }


# ---------------------------------------------------------------------------
# Full chain: generated candidate -> rights cleared -> proposal_ready
# ---------------------------------------------------------------------------


async def test_generated_candidate_approval_blocked_until_rights_cleared(
    auth_client,
    db_session,
    tmp_path,
    no_illustration_dispatch,
    illustration_storage,
    consistency_fixtures_enabled,
):
    user, novel = await _seed_testuser_novel(db_session)
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate",
        json=_generation_request().model_dump(mode="json"),
    )
    assert resp.status_code == 201
    job_id = resp.json()["job"]["id"]
    await db_session.commit()

    runtime, transport, storage = _runtime(tmp_path)
    await run_illustration_worker(job_id, runtime=runtime)

    asset = await db_session.scalar(
        select(AssetRevision).where(AssetRevision.job_id == job_id)
    )
    assert asset is not None
    assert asset.approval_state == "candidate"
    assert asset.rights_status == "unreviewed"

    # A visible consistency report exists (D-33-04 review signal).
    eval_resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/assets/{asset.id}/consistency/evaluate",
        json={
            "character_key": "arin",
            "scene_key": "ch1",
            "report_key": "arin:ch1",
            "identity_attributes": [
                "black_hair",
                "amber_eyes",
                "lean_build",
                "scar_left_brow",
            ],
            "style_attributes": ["ink_painting", "warm_palette", "soft_lighting"],
            "negative_constraints_present": [],
        },
    )
    assert eval_resp.status_code == 201
    await db_session.commit()

    # Generated candidate has unreviewed rights -> approval fails closed.
    blocked = await auth_client.post(
        _review_url(novel.id, asset.id),
        json=_approve_payload(asset.id),
    )
    assert blocked.status_code == 409
    assert "rights_unresolved" in blocked.json()["detail"]
    asset = await db_session.scalar(
        select(AssetRevision).where(AssetRevision.id == asset.id)
    )
    assert asset.approval_state == "candidate"

    # The rights authority clears the asset (raw SQL: the ORM append-only guard
    # intentionally forbids in-place rights mutation; rights clearing is an
    # out-of-scope external authority in Phase 33).
    await db_session.execute(
        text("UPDATE asset_revisions SET rights_status = 'cleared' WHERE id = :id"),
        {"id": asset.id},
    )
    await db_session.commit()
    # Refresh the shared ORM object so the API's append_event sees the cleared
    # rights (SQLAlchemy would otherwise return the stale identity-map object).
    await db_session.refresh(asset)

    approved = await auth_client.post(
        _review_url(novel.id, asset.id),
        json=_approve_payload(asset.id),
    )
    assert approved.status_code == 200, approved.json()
    body = approved.json()
    assert body["asset"]["approval_state"] == "proposal_ready"
    assert body["asset"]["rights_status"] == "cleared"
    assert body["envelope"]["asset"]["approval_state"] == "proposal_ready"
    assert body["envelope"]["approval_gate"] is None
    assert len(body["envelope"]["review_events"]) == 1
    assert body["envelope"]["review_events"][0]["action"] == "approve"
    assert body["envelope"]["review_events"][0]["to_approval_state"] == "proposal_ready"

    # Phase 34 consumer contract: proposal_ready + cleared rights can be frozen.
    asset = await db_session.scalar(
        select(AssetRevision).where(AssetRevision.id == asset.id)
    )
    assert asset.approval_state == "proposal_ready"
    assert asset.approved_by == "owner"
    ref = build_proposal_ref(asset)
    assert ref.approval_state == "proposal_ready"
    assert ref.rights_status == "cleared"
    assert ref.approved_by == "owner"

    # No active-pointer / publish: the review envelope has no published flag.
    assert "publish" not in body["envelope"]


async def test_generated_candidate_is_candidate_only_in_gallery(
    auth_client,
    db_session,
    tmp_path,
    no_illustration_dispatch,
    illustration_storage,
    consistency_fixtures_enabled,
):
    user, novel = await _seed_testuser_novel(db_session)
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate",
        json=_generation_request().model_dump(mode="json"),
    )
    assert resp.status_code == 201
    job_id = resp.json()["job"]["id"]
    await db_session.commit()
    runtime, transport, storage = _runtime(tmp_path)
    await run_illustration_worker(job_id, runtime=runtime)

    asset = await db_session.scalar(
        select(AssetRevision).where(AssetRevision.job_id == job_id)
    )
    assert asset is not None
    # Persist a consistency report so the gallery/envelope shows the evidence.
    eval_resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/assets/{asset.id}/consistency/evaluate",
        json={
            "character_key": "arin",
            "scene_key": "ch1",
            "report_key": "arin:ch1",
            "identity_attributes": [
                "black_hair",
                "amber_eyes",
                "lean_build",
                "scar_left_brow",
            ],
            "style_attributes": ["ink_painting", "warm_palette", "soft_lighting"],
            "negative_constraints_present": [],
        },
    )
    assert eval_resp.status_code == 201
    await db_session.commit()

    gallery = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/gallery"
    )
    assert gallery.status_code == 200
    items = gallery.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["asset"]["approval_state"] == "candidate"
    assert item["job"]["status"] == "succeeded"
    # Rights unreviewed -> the candidate-only approval gate surfaces why.
    assert item["approval_gate"]["ok"] is False
    assert item["approval_gate"]["reason_code"] == "rights_unresolved"
    assert len(item["review_events"]) == 0

    envelope = await auth_client.get(_review_url(novel.id, item["asset"]["id"]))
    assert envelope.status_code == 200
    env = envelope.json()
    assert env["attempts"][0]["status"] == "succeeded"
    assert env["budget"]["settled_usage"]["usage_unknown"] is False
    assert env["budget"]["reservation_status"] == "settled"
    assert env["consistency"]["verdict"] == "pass"


# ---------------------------------------------------------------------------
# Fail-closed gates at service/API level
# ---------------------------------------------------------------------------


async def test_approve_fails_closed_on_each_missing_precondition(
    auth_client, db_session
):
    user, novel = await _seed_testuser_novel(db_session)
    cases = [
        # (job_status, rights_status, with_budget, with_report, expected_code)
        ("failed", "cleared", True, True, "job_not_succeeded"),
        ("succeeded", "unreviewed", True, True, "rights_unresolved"),
        ("succeeded", "cleared", False, True, "budget_unsettled"),
        ("succeeded", "cleared", True, False, "consistency_missing"),
    ]
    for idx, (job_status, rights, budget, report, code) in enumerate(cases):
        asset = await _seed_complete_candidate(
            db_session,
            user,
            novel,
            job_status=job_status,
            rights_status=rights,
            with_budget=budget,
            with_report=report,
            seed_id=f"job-review-{idx}",
        )
        await db_session.commit()
        resp = await auth_client.post(
            _review_url(novel.id, asset.id),
            json=_approve_payload(asset.id, event_key=f"ev-{asset.id}"),
        )
        assert resp.status_code == 409, (idx, resp.json())
        assert code in resp.json()["detail"], (idx, resp.json())
        asset = await db_session.scalar(
            select(AssetRevision).where(AssetRevision.id == asset.id)
        )
        assert asset.approval_state == "candidate"


async def test_approve_requires_job_and_from_state_match(db_session):
    user, novel = await _seed_user_and_novel(db_session, "ill_review_state")
    asset = await _seed_complete_candidate(db_session, user, novel)
    await db_session.commit()

    service = IllustrationReviewService(db_session)
    from app.schemas.illustration import (
        IllustrationActorSource,
        IllustrationReviewEventInput,
    )

    # from_approval_state drift from the persisted candidate fails closed.
    # Use a state whose action is legal but which does not match the candidate.
    drifted = IllustrationReviewEventInput(
        owner_id=user.id,
        novel_id=novel.id,
        asset_revision_id=asset.id,
        event_key="ev-drift",
        action=IllustrationReviewAction.SUPERSEDE,
        actor_source=IllustrationActorSource.HUMAN,
        actor="owner",
        reason="drift",
        from_approval_state=IllustrationApprovalState.REJECTED,
    )
    try:
        await service.append_event(owner_id=user.id, novel_id=novel.id, event=drifted)
        raise AssertionError("expected from-state drift to fail closed")
    except IllustrationReviewGateError as exc:
        assert "does not match" in str(exc)

    # Cross-owner asset is indistinguishable from not found.
    other_user, other_novel = await _seed_user_and_novel(db_session, "ill_review_xowner")
    other_asset = await _seed_complete_candidate(
        db_session, other_user, other_novel, seed_id="job-xowner"
    )
    await db_session.commit()
    try:
        await service.append_event(
            owner_id=user.id,
            novel_id=novel.id,
            event=IllustrationReviewEventInput(
                owner_id=user.id,
                novel_id=novel.id,
                asset_revision_id=other_asset.id,
                event_key="ev-xowner",
                action=IllustrationReviewAction.APPROVE,
                actor_source=IllustrationActorSource.HUMAN,
                actor="owner",
                reason="xowner",
                from_approval_state=IllustrationApprovalState.CANDIDATE,
            ),
        )
        raise AssertionError("expected cross-owner asset to be not found")
    except IllustrationReviewNotFound:
        pass


# ---------------------------------------------------------------------------
# Idempotent replay + explicit reject/supersede/needs_relink
# ---------------------------------------------------------------------------


async def test_approve_is_idempotent_and_stale_from_state_fails(
    auth_client, db_session
):
    user, novel = await _seed_testuser_novel(db_session)
    asset = await _seed_complete_candidate(db_session, user, novel)
    await db_session.commit()

    first = await auth_client.post(
        _review_url(novel.id, asset.id),
        json=_approve_payload(asset.id),
    )
    assert first.status_code == 200
    assert first.json()["asset"]["approval_state"] == "proposal_ready"

    # Same event_key replays: no second event, no projection change.
    again = await auth_client.post(
        _review_url(novel.id, asset.id),
        json=_approve_payload(asset.id),
    )
    assert again.status_code == 200
    assert again.json()["asset"]["approval_state"] == "proposal_ready"
    events = (
        await db_session.scalars(
            select(IllustrationReviewEvent).where(
                IllustrationReviewEvent.asset_revision_id == asset.id
            )
        )
    ).all()
    assert len(events) == 1

    # A different event_key from the now-stale candidate state fails closed.
    stale = await auth_client.post(
        _review_url(novel.id, asset.id),
        json=_approve_payload(asset.id, event_key="ev-stale"),
    )
    assert stale.status_code == 409
    assert "does not match" in stale.json()["detail"]


async def test_reject_then_supersede_then_needs_relink(
    auth_client, db_session
):
    user, novel = await _seed_testuser_novel(db_session)
    asset = await _seed_complete_candidate(db_session, user, novel)
    await db_session.commit()

    # reject: candidate -> rejected (append-only, no publish).
    rejected = await auth_client.post(
        _review_url(novel.id, asset.id),
        json=_approve_payload(
            asset.id,
            event_key=f"ev-{asset.id}-reject",
            action="reject",
            from_approval_state="candidate",
        ),
    )
    assert rejected.status_code == 200
    assert rejected.json()["asset"]["approval_state"] == "rejected"

    # supersede: rejected -> superseded.
    superseded = await auth_client.post(
        _review_url(novel.id, asset.id),
        json=_approve_payload(
            asset.id,
            event_key=f"ev-{asset.id}-supersede",
            action="supersede",
            from_approval_state="rejected",
        ),
    )
    assert superseded.status_code == 200
    assert superseded.json()["asset"]["approval_state"] == "superseded"

    # superseded is terminal: no legal action remains (404-equivalent gate).
    from app.schemas.illustration import is_legal_illustration_review_action

    assert is_legal_illustration_review_action("superseded", "approve") is False
    assert is_legal_illustration_review_action("superseded", "needs_relink") is False

    # needs_relink: rejected -> candidate (relink reopens a candidate).
    reopened = await _seed_complete_candidate(
        db_session, user, novel, seed_id="job-reopen"
    )
    await db_session.commit()
    relink = await auth_client.post(
        _review_url(novel.id, reopened.id),
        json=_approve_payload(
            reopened.id,
            event_key=f"ev-{reopened.id}-reject",
            action="reject",
            from_approval_state="candidate",
        ),
    )
    assert relink.status_code == 200
    relink2 = await auth_client.post(
        _review_url(novel.id, reopened.id),
        json=_approve_payload(
            reopened.id,
            event_key=f"ev-{reopened.id}-relink",
            action="needs_relink",
            from_approval_state="rejected",
        ),
    )
    assert relink2.status_code == 200
    assert relink2.json()["asset"]["approval_state"] == "candidate"

    # Append-only history records every action in order.
    events = (
        await db_session.scalars(
            select(IllustrationReviewEvent)
            .where(IllustrationReviewEvent.asset_revision_id == reopened.id)
            .order_by(IllustrationReviewEvent.id.asc())
        )
    ).all()
    assert [e.action for e in events] == ["reject", "needs_relink"]
    assert [e.to_approval_state for e in events] == ["rejected", "candidate"]


async def test_review_event_rows_are_append_only(db_session):
    user, novel = await _seed_user_and_novel(db_session, "ill_review_immutable")
    asset = await _seed_complete_candidate(db_session, user, novel)
    await db_session.commit()

    service = IllustrationReviewService(db_session)
    from app.schemas.illustration import (
        IllustrationActorSource,
        IllustrationReviewEventInput,
    )

    await service.append_event(
        owner_id=user.id,
        novel_id=novel.id,
        event=IllustrationReviewEventInput(
            owner_id=user.id,
            novel_id=novel.id,
            asset_revision_id=asset.id,
            event_key="ev-immutable",
            action=IllustrationReviewAction.REJECT,
            actor_source=IllustrationActorSource.HUMAN,
            actor="owner",
            reason="reject",
            from_approval_state=IllustrationApprovalState.CANDIDATE,
        ),
    )
    await db_session.commit()

    row = await db_session.scalar(
        select(IllustrationReviewEvent).where(
            IllustrationReviewEvent.event_key == "ev-immutable"
        )
    )
    assert row is not None
    # In-place mutation of an append-only review row is rejected by the guard
    # (the before_update hook raises ValueError during flush).
    row.reason = "mutated"
    with pytest.raises(ValueError, match="immutable"):
        await db_session.flush()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# Owner-scoped reads + cross-owner isolation
# ---------------------------------------------------------------------------


async def test_review_cross_owner_is_not_found(auth_client, db_session):
    await _demote_testuser(db_session)
    owner, novel = await _seed_user_and_novel(db_session, "ill_review_other")
    asset = await _seed_complete_candidate(
        db_session, owner, novel, seed_id="job-xowner2"
    )
    await db_session.commit()

    approve = await auth_client.post(
        _review_url(novel.id, asset.id),
        json=_approve_payload(asset.id),
    )
    assert approve.status_code == 404
    envelope = await auth_client.get(_review_url(novel.id, asset.id))
    assert envelope.status_code == 404
    gallery = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/gallery"
    )
    assert gallery.status_code == 404


async def test_gallery_and_envelope_via_service(db_session):
    user, novel = await _seed_user_and_novel(db_session, "ill_review_service")
    asset = await _seed_complete_candidate(db_session, user, novel)
    await db_session.commit()

    gallery = await build_gallery(
        db_session, owner_id=user.id, novel_id=novel.id
    )
    assert gallery.total == 1
    item = gallery.items[0]
    assert item.asset.approval_state == "candidate"
    assert item.job.status == "succeeded"
    assert item.consistency is not None and item.consistency.verdict == "pass"
    assert item.approval_gate is not None and item.approval_gate.ok is True

    envelope = await build_review_envelope(
        db_session, owner_id=user.id, novel_id=novel.id, asset_id=asset.id
    )
    assert envelope.budget is not None
    assert envelope.budget.settled_usage["usage_unknown"] is False
    assert envelope.budget.reservation_status == "settled"
    assert len(envelope.attempts) == 0  # direct seeding records no provider attempt
    assert envelope.consistency.verdict == "pass"
    assert envelope.review_events == []
