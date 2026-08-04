"""Phase 33-02 illustration generation integration tests (REQ-VIS-04).

Covers the 33-VALIDATION.md integration matrix and the PLAN acceptance gates:
- mock generation success: one durable job → one candidate AssetRevision with
  immutable source/prompt/model lineage and budget/cost settlement;
- idempotent jobs: a duplicate idempotency key replays the existing job and a
  re-dispatch never calls the provider twice or charges twice;
- explicit failures: timeout/5xx/disconnect are ``outcome_unknown`` and
  reconcile by request id/hash; an empty asset is a failure, never a success;
- bounded retry: timeouts retry within a reason-coded budget and cannot repeat
  a successful attempt;
- budget exhaustion: the novel-scoped ledger fails closed (paused_budget);
- generation entrypoint gate: only an **approved** and **non-stale**
  PromptRevision can generate; cross-owner/novel access is 404-equivalent;
- candidate-only assets: generated output stays ``candidate`` with bytes
  addressable through the owner-scoped API only.

Fixtures: the SQLite in-memory ``db_session`` / ``auth_client`` from the
top-level conftest; the durable worker runs against the same engine through
``TestSessionLocal``. No external service is required.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.illustrations import (
    IllustrationGenerationRequest,
    IllustrationJobService,
    set_illustration_asset_storage,
)
from app.models import Novel, User
from app.models.illustration import AssetRevision
from app.models.illustration_job import (
    IllustrationAttempt,
    IllustrationBudgetLedger,
    IllustrationBudgetReservation,
    IllustrationJob,
)
from app.models.prompt_revision import PromptRevision
from app.models.scene_spec import SceneSpecVersion
from app.models.visual_bible import VisualBibleVersion
from app.services.illustrations.budget import IllustrationBudgetPolicy
from app.services.illustrations.gateway import (
    IllustrationGateway,
    MockIllustrationTransport,
)
from app.services.illustrations.storage import AssetStorage
from app.services.illustrations.worker import (
    DEFAULT_ILLUSTRATION_POLICY,
    IllustrationWorkerRuntime,
    default_illustration_price_snapshot,
    run_illustration_worker,
)
from app.services.key_scenes.boundaries import SceneBoundaryService
from tests.conftest import TestSessionLocal

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64
HEX64_E = "e" * 64

VB_HASH = "1" * 64
VB_HASH_2 = "2" * 64
SCENE_SPEC_HASH = "3" * 64
SNAPSHOT_HASH = "4" * 64
PROMPT_HASH = "5" * 64
PROMPT_HASH_2 = "6" * 64
INPUT_HASH = "7" * 64


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Chain setup: approved Visual Bible -> SceneSpec -> PromptRevision
# ---------------------------------------------------------------------------


async def _source_snapshot_hash(db: AsyncSession, owner_id: int, novel_id: int) -> str:
    snapshot_hash, _ = await SceneBoundaryService(db).load_source_snapshot(
        owner_id=owner_id, novel_id=novel_id
    )
    return snapshot_hash


async def _seed_user_and_novel(db: AsyncSession, username: str) -> tuple[User, Novel]:
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db.add(user)
    await db.flush()
    novel = Novel(title=f"Illustration Novel {username}", owner_id=user.id)
    db.add(novel)
    await db.flush()
    return user, novel


async def _get_testuser(db: AsyncSession) -> User:
    """Return the user created by the ``auth_client`` fixture."""
    user = await db.scalar(select(User).where(User.username == "testuser"))
    if user is None:
        user = User(
            username="testuser", email="test@example.com", hashed_password="hash"
        )
        db.add(user)
        await db.flush()
    return user


async def _seed_testuser_novel(db: AsyncSession) -> tuple[User, Novel]:
    """Create a novel owned by the authenticated ``auth_client`` user."""
    user = await _get_testuser(db)
    novel = Novel(title="Illustration Novel testuser", owner_id=user.id)
    db.add(novel)
    await db.flush()
    return user, novel


async def _demote_testuser(db: AsyncSession) -> None:
    """The first registered user is the bootstrap superuser; demote it so
    owner-scope tests exercise the real cross-owner 404 path."""
    user = await _get_testuser(db)
    if user.is_superuser:
        user.is_superuser = False
        await db.flush()


@pytest_asyncio.fixture
async def no_illustration_dispatch():
    """Disable BackgroundTasks dispatch so the worker is driven explicitly."""
    from app.main import app as fastapi_app

    fastapi_app.state.illustration_dispatch_enabled = False
    yield
    # Restore the default (dispatch enabled) after the test.
    fastapi_app.state.illustration_dispatch_enabled = True


@pytest_asyncio.fixture
def illustration_storage(tmp_path) -> AssetStorage:
    """Override the API asset-bytes backend with a per-test tmp store."""
    storage = AssetStorage(tmp_path / "assets")
    set_illustration_asset_storage(storage)
    yield storage
    set_illustration_asset_storage(None)


async def _make_prompt_chain(
    db: AsyncSession,
    user: User,
    novel: Novel,
    *,
    approved: bool = True,
    vb_hash: str = VB_HASH,
    snapshot_hash: str | None = None,
    prompt_hash: str = PROMPT_HASH,
) -> tuple[PromptRevision, VisualBibleVersion, SceneSpecVersion, str]:
    """Create an approved Visual Bible revision + SceneSpec + PromptRevision.

    Returns (prompt, vb, spec, snapshot_hash). The spec/prompt freeze the same
    Visual Bible manifest hash and the current source snapshot so the prompt is
    fresh (non-stale) unless a caller overrides ``vb_hash``.
    """
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
        scene_candidate_hash=HEX64_D,
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


# ---------------------------------------------------------------------------
# Worker runtime builders
# ---------------------------------------------------------------------------


def _runtime(
    tmp_path,
    *,
    mode: str = "success",
    policy: IllustrationBudgetPolicy = DEFAULT_ILLUSTRATION_POLICY,
    max_attempts: int = 3,
    fail_first: int = 0,
) -> tuple[IllustrationWorkerRuntime, MockIllustrationTransport, AssetStorage]:
    storage = AssetStorage(tmp_path / "assets")
    transport = MockIllustrationTransport(mode=mode, fail_first=fail_first)
    runtime = IllustrationWorkerRuntime(
        sessions=TestSessionLocal,
        gateway=IllustrationGateway(transport),
        storage=storage,
        budget_policy=policy,
        max_attempts=max_attempts,
    )
    return runtime, transport, storage


def _generation_request(*, model: str = "mock-img-v1", **overrides) -> IllustrationGenerationRequest:
    payload = {
        "prompt_revision_id": 1,
        "job_key": "job-arin-bamboo",
        "provider": "mock",
        "model": model,
        "width": 1024,
        "height": 1024,
    }
    payload.update(overrides)
    return IllustrationGenerationRequest.model_validate(payload)


async def _create_job(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    request: IllustrationGenerationRequest,
) -> tuple[IllustrationJob, bool]:
    return await IllustrationJobService(db).create_job(
        owner_id=owner_id, novel_id=novel_id, request=request
    )


async def _count(db: AsyncSession, model) -> int:
    return int(await db.scalar(select(func.count(model.id))) or 0)


async def _reload_job(db: AsyncSession, job: IllustrationJob) -> IllustrationJob:
    """Reload a job row from the DB (the worker commits in another session)."""
    row = await db.scalar(
        select(IllustrationJob)
        .where(IllustrationJob.id == job.id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Generation entrypoint gate (approved-only, stale, cross-owner)
# ---------------------------------------------------------------------------


async def test_generate_rejects_unapproved_prompt(auth_client, db_session):
    user, novel = await _seed_testuser_novel(db_session)
    await _make_prompt_chain(db_session, user, novel, approved=False)
    await db_session.flush()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate",
        json=_generation_request().model_dump(mode="json"),
    )
    assert resp.status_code == 409
    assert "approved" in resp.json()["detail"]
    assert await _count(db_session, IllustrationJob) == 0


async def test_generate_rejects_stale_prompt(auth_client, db_session):
    user, novel = await _seed_testuser_novel(db_session)
    prompt, vb, spec, snapshot_hash = await _make_prompt_chain(
        db_session, user, novel, approved=True
    )
    # A newer approved Visual Bible revision supersedes the one the prompt was
    # compiled against -> the prompt is stale and must fail closed.
    await _make_prompt_chain(
        db_session, user, novel, approved=True, vb_hash=VB_HASH_2
    )
    await db_session.flush()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate",
        json=_generation_request().model_dump(mode="json"),
    )
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"]
    assert await _count(db_session, IllustrationJob) == 0


async def test_generate_cross_owner_is_not_found(auth_client, db_session):
    await _demote_testuser(db_session)
    # The chain belongs to a different owner; the authenticated testuser cannot
    # even learn the novel exists (404-equivalent, D-33-03 containment).
    owner, novel = await _seed_user_and_novel(db_session, "other-owner")
    await _make_prompt_chain(db_session, owner, novel, approved=True)
    await db_session.flush()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate",
        json=_generation_request().model_dump(mode="json"),
    )
    assert resp.status_code == 404


async def test_generate_rejects_unknown_provider(auth_client, db_session):
    user, novel = await _seed_testuser_novel(db_session)
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.flush()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate",
        json=_generation_request(provider="openai").model_dump(mode="json"),
    )
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Durable job creation + idempotency (API level)
# ---------------------------------------------------------------------------


async def test_generate_creates_durable_job_idempotently(
    auth_client, db_session, no_illustration_dispatch
):
    user, novel = await _seed_testuser_novel(db_session)
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.flush()

    payload = _generation_request().model_dump(mode="json")
    first = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate", json=payload
    )
    assert first.status_code == 201
    first_job = first.json()["job"]
    assert first_job["status"] == "queued"
    assert first.json()["replayed"] is False

    second = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate", json=payload
    )
    assert second.status_code == 201
    assert second.json()["job"]["id"] == first_job["id"]
    assert second.json()["replayed"] is True

    assert await _count(db_session, IllustrationJob) == 1


# ---------------------------------------------------------------------------
# Worker: mock success, durable cost/budget, immutable asset lineage
# ---------------------------------------------------------------------------


async def test_worker_generates_candidate_asset_with_full_lineage(
    db_session, tmp_path
):
    user, novel = await _seed_user_and_novel(db_session, "ill_success")
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    request = _generation_request()
    job, replayed = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id, request=request
    )
    assert replayed is False
    await db_session.commit()

    runtime, transport, storage = _runtime(tmp_path)
    await run_illustration_worker(job.id, runtime=runtime)

    job = await _reload_job(db_session, job)
    assert job.status == "succeeded"
    assert job.status_reason == "generated"
    assert job.response_hash is not None

    attempts = list(
        (
            await db_session.scalars(
                select(IllustrationAttempt)
                .where(IllustrationAttempt.job_id == job.id)
                .order_by(IllustrationAttempt.attempt_number)
            )
        ).all()
    )
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.status == "succeeded"
    assert attempt.cost_usd is not None and attempt.cost_usd > 0
    assert attempt.usage.get("input_tokens") == 120
    assert attempt.provider_request_id.startswith("mock-req-")

    assets = list(
        (
            await db_session.scalars(
                select(AssetRevision).where(AssetRevision.job_id == job.id)
            )
        ).all()
    )
    assert len(assets) == 1
    asset = assets[0]
    # Candidate-only: never approved and never canon.
    assert asset.approval_state == "candidate"
    assert asset.rights_status == "unreviewed"
    # Immutable lineage replays exactly from the job (D-33-01/03).
    assert asset.scene_spec_hash == job.scene_spec_hash
    assert asset.prompt_revision_hash == job.prompt_revision_hash
    assert asset.visual_bible_revision_hash == job.visual_bible_revision_hash
    assert asset.source_snapshot_hash == job.source_snapshot_hash
    assert asset.config_hash == job.config_hash
    assert asset.cutoff_chapter == job.cutoff_chapter
    assert asset.model_lineage == job.model_lineage
    assert asset.bytes_hash == job.response_hash

    # Bytes exist in content-hash storage and replay hash/size.
    stored = storage.read(
        owner_id=user.id, novel_id=novel.id, storage_key=asset.storage_key
    )
    assert stored.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(stored) == asset.size_bytes
    assert hashlib.sha256(stored).hexdigest() == asset.bytes_hash
    assert asset.size_bytes > 0

    # Durable budget evidence: ledger + one settled reservation with a price
    # snapshot and explicit settled usage.
    ledger = await db_session.scalar(
        select(IllustrationBudgetLedger).where(
            IllustrationBudgetLedger.owner_id == user.id,
            IllustrationBudgetLedger.novel_id == novel.id,
        )
    )
    assert ledger is not None
    assert ledger.settled_calls == 1
    reservation = await db_session.scalar(
        select(IllustrationBudgetReservation).where(
            IllustrationBudgetReservation.ledger_id == ledger.id
        )
    )
    assert reservation is not None
    assert reservation.status == "settled"
    assert reservation.settled_usage.get("usage_unknown") is False
    assert reservation.price_snapshot.get("provider") == "mock"
    assert reservation.reservation_key == f"job:{job.id}:attempt:1"


async def test_duplicate_job_and_redispatch_charge_once(db_session, tmp_path):
    user, novel = await _seed_user_and_novel(db_session, "ill_idem")
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    request = _generation_request()
    job, replayed = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id, request=request
    )
    assert replayed is False
    duplicate, replayed = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id, request=request
    )
    assert duplicate.id == job.id
    assert replayed is True
    await db_session.commit()

    runtime, transport, storage = _runtime(tmp_path)
    await run_illustration_worker(job.id, runtime=runtime)
    # Re-dispatch of the already-succeeded job is an idempotent completion.
    await run_illustration_worker(job.id, runtime=runtime)

    job = await _reload_job(db_session, job)
    assert job.status == "succeeded"

    attempts = list(
        (
            await db_session.scalars(
                select(IllustrationAttempt).where(IllustrationAttempt.job_id == job.id)
            )
        ).all()
    )
    assert len(attempts) == 1
    assets = list(
        (
            await db_session.scalars(
                select(AssetRevision).where(AssetRevision.job_id == job.id)
            )
        ).all()
    )
    assert len(assets) == 1
    # One provider call, one settled reservation: one charge for the duplicate.
    assert transport.calls == 1
    ledger = await db_session.scalar(
        select(IllustrationBudgetLedger).where(
            IllustrationBudgetLedger.owner_id == user.id,
            IllustrationBudgetLedger.novel_id == novel.id,
        )
    )
    assert ledger is not None and ledger.settled_calls == 1


async def test_worker_retries_timeout_then_succeeds(db_session, tmp_path):
    user, novel = await _seed_user_and_novel(db_session, "ill_retry")
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    job, _ = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id,
        request=_generation_request(),
    )
    await db_session.commit()

    # First two calls time out (outcome unknown), the third succeeds.
    runtime, transport, storage = _runtime(tmp_path, mode="success", fail_first=2)
    await run_illustration_worker(job.id, runtime=runtime)

    job = await _reload_job(db_session, job)
    assert job.status == "succeeded"
    attempts = list(
        (
            await db_session.scalars(
                select(IllustrationAttempt)
                .where(IllustrationAttempt.job_id == job.id)
                .order_by(IllustrationAttempt.attempt_number)
            )
        ).all()
    )
    assert [a.status for a in attempts] == [
        "outcome_unknown",
        "outcome_unknown",
        "succeeded",
    ]
    assert all(a.error_code == "TimeoutError" for a in attempts[:2])
    assert transport.calls == 3


async def test_worker_outcome_unknown_when_retries_exhausted(db_session, tmp_path):
    user, novel = await _seed_user_and_novel(db_session, "ill_unknown")
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    job, _ = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id,
        request=_generation_request(),
    )
    await db_session.commit()

    # Every call times out: the job ends explicitly outcome_unknown, never a
    # false success, and no empty asset is written.
    runtime, transport, storage = _runtime(tmp_path, mode="timeout", max_attempts=2)
    await run_illustration_worker(job.id, runtime=runtime)

    job = await _reload_job(db_session, job)
    assert job.status == "outcome_unknown"
    assert job.error_code == "provider_outcome_unknown"
    attempts = list(
        (
            await db_session.scalars(
                select(IllustrationAttempt).where(IllustrationAttempt.job_id == job.id)
            )
        ).all()
    )
    assert [a.status for a in attempts] == ["outcome_unknown", "outcome_unknown"]
    assert await _count(db_session, AssetRevision) == 0


async def test_worker_empty_asset_never_becomes_success(db_session, tmp_path):
    user, novel = await _seed_user_and_novel(db_session, "ill_empty")
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    job, _ = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id,
        request=_generation_request(),
    )
    await db_session.commit()

    runtime, transport, storage = _runtime(tmp_path, mode="empty", max_attempts=2)
    await run_illustration_worker(job.id, runtime=runtime)

    job = await _reload_job(db_session, job)
    assert job.status == "failed"
    assert job.error_code == "empty_asset"
    attempts = list(
        (
            await db_session.scalars(
                select(IllustrationAttempt).where(IllustrationAttempt.job_id == job.id)
            )
        ).all()
    )
    assert [a.status for a in attempts] == ["failed", "failed"]
    assert all(a.error_code == "empty_asset" for a in attempts)
    # No empty-success asset exists.
    assert await _count(db_session, AssetRevision) == 0


async def test_worker_budget_exhaustion_pauses_job(db_session, tmp_path):
    user, novel = await _seed_user_and_novel(db_session, "ill_budget")
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    tight = IllustrationBudgetPolicy(max_calls=1, max_cost_usd=Decimal("1.00"))
    runtime, transport, storage = _runtime(tmp_path, policy=tight)

    first, _ = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id,
        request=_generation_request(model="mock-img-v1"),
    )
    await db_session.commit()
    await run_illustration_worker(first.id, runtime=runtime)

    # A different lineage (different config hash) still hits the same
    # novel-scoped ledger; the second call exceeds max_calls=1 -> paused.
    second, _ = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id,
        request=_generation_request(model="mock-img-v2", job_key="job-arin-2"),
    )
    await db_session.commit()
    await run_illustration_worker(second.id, runtime=runtime)

    first = await _reload_job(db_session, first)
    second = await _reload_job(db_session, second)
    assert first.status == "succeeded"
    assert second.status == "paused_budget"
    assert second.error_code == "BudgetExceeded"
    assert await _count(db_session, AssetRevision) == 1
    ledger = await db_session.scalar(
        select(IllustrationBudgetLedger).where(
            IllustrationBudgetLedger.owner_id == user.id,
            IllustrationBudgetLedger.novel_id == novel.id,
        )
    )
    assert ledger is not None and ledger.settled_calls == 1


async def test_worker_fails_closed_when_prompt_becomes_stale_after_create(
    db_session, tmp_path
):
    user, novel = await _seed_user_and_novel(db_session, "ill_stale_worker")
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    job, _ = await _create_job(
        db_session, owner_id=user.id, novel_id=novel.id,
        request=_generation_request(),
    )
    await db_session.commit()

    # A newer approved Visual Bible revision lands before the worker runs:
    # the server-side gate fails closed and no provider call happens.
    await _make_prompt_chain(db_session, user, novel, approved=True, vb_hash=VB_HASH_2)
    await db_session.commit()

    runtime, transport, storage = _runtime(tmp_path)
    await run_illustration_worker(job.id, runtime=runtime)

    job = await _reload_job(db_session, job)
    assert job.status == "failed"
    assert job.error_code == "stale_prompt"
    assert transport.calls == 0
    assert await _count(db_session, AssetRevision) == 0


# ---------------------------------------------------------------------------
# Owner-scoped candidate asset read API (bytes + list/detail)
# ---------------------------------------------------------------------------


async def test_asset_api_is_owner_scoped_and_candidate_only(
    auth_client, db_session, tmp_path, no_illustration_dispatch, illustration_storage
):
    user, novel = await _seed_testuser_novel(db_session)
    await _make_prompt_chain(db_session, user, novel, approved=True)
    await db_session.commit()

    runtime, transport, storage = _runtime(tmp_path)
    resp = await auth_client.post(
        f"/api/novels/{novel.id}/illustrations/generate",
        json=_generation_request().model_dump(mode="json"),
    )
    assert resp.status_code == 201
    job_id = resp.json()["job"]["id"]
    await db_session.commit()

    await run_illustration_worker(job_id, runtime=runtime)

    listing = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/assets"
    )
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["approval_state"] == "candidate"
    assert items[0]["rights_status"] == "unreviewed"
    assert items[0]["provider"] == "mock"
    assert len(items[0]["bytes_hash"]) == 64

    asset_id = items[0]["id"]
    detail = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/assets/{asset_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == asset_id

    job_view = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/jobs/{job_id}"
    )
    assert job_view.status_code == 200
    assert job_view.json()["status"] == "succeeded"

    # Owner-scoped bytes: the candidate gallery can fetch the exact payload.
    bytes_resp = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/assets/{asset_id}/bytes"
    )
    assert bytes_resp.status_code == 200
    assert bytes_resp.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(bytes_resp.content).hexdigest() == items[0]["bytes_hash"]


async def test_asset_bytes_cross_owner_404(
    auth_client, db_session, tmp_path, illustration_storage
):
    await _demote_testuser(db_session)
    owner, novel = await _seed_user_and_novel(db_session, "other-owner")
    await _make_prompt_chain(db_session, owner, novel, approved=True)
    await db_session.commit()

    runtime, transport, storage = _runtime(tmp_path)
    job, _ = await _create_job(
        db_session, owner_id=owner.id, novel_id=novel.id,
        request=_generation_request(),
    )
    await db_session.commit()
    job_id = job.id
    await run_illustration_worker(job_id, runtime=runtime)

    asset = await db_session.scalar(
        select(AssetRevision).where(AssetRevision.job_id == job_id)
    )
    assert asset is not None
    # The authenticated testuser cannot read another owner's asset bytes.
    asset = await db_session.scalar(
        select(AssetRevision).where(AssetRevision.job_id == job_id)
    )
    assert asset is not None
    # The authenticated non-superuser testuser cannot read another owner's
    # asset bytes or even list the foreign novel's assets (404-equivalent).
    resp = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/assets/{asset.id}/bytes"
    )
    assert resp.status_code == 404
    resp = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/assets"
    )
    assert resp.status_code == 404
