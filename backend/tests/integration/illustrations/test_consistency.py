"""Phase 33-03 consistency API integration tests (REQ-VIS-04, D-33-04).

Covers the 33-VALIDATION ``illustration-consistency`` fixture and the API
surface:
- evaluate a generated candidate against the frozen per-character fixture set
  and persist a versioned report whose source/prompt/model/fixture lineage
  matches the asset (report replayable);
- identity/style drift and negative-constraint violations map to
  concern/fail verdicts and the report keeps evaluator/model/fixture lineage;
- idempotent replay: the same report_key + evidence returns one row;
- no evaluator configured -> explicit ``unavailable`` (fail closed, no score);
- reports are evidence only: evaluating never approves the candidate (the
  asset stays ``candidate``) and never touches the Visual Bible;
- all reads are owner-scoped: a cross-owner asset is indistinguishable from
  not found (404).

Fixtures: the SQLite in-memory ``db_session`` / ``auth_client`` from the
top-level conftest; the durable worker runs against the same engine through
``TestSessionLocal``. No external service is required.
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.illustrations import (
    IllustrationGenerationRequest,
    set_illustration_asset_storage,
    set_illustration_consistency_fixtures,
)
from app.models import Novel, User
from app.models.illustration import AssetRevision, ConsistencyReport
from app.models.illustration_job import IllustrationJob
from app.models.prompt_revision import PromptRevision
from app.models.scene_spec import SceneSpecVersion
from app.models.visual_bible import VisualBibleVersion
from app.services.illustrations.budget import IllustrationBudgetPolicy
from app.services.illustrations.consistency import (
    mock_consistency_fixture_registry,
)
from app.services.illustrations.gateway import (
    IllustrationGateway,
    MockIllustrationTransport,
)
from app.services.illustrations.storage import AssetStorage
from app.services.illustrations.worker import (
    DEFAULT_ILLUSTRATION_POLICY,
    IllustrationWorkerRuntime,
    run_illustration_worker,
)
from app.services.key_scenes.boundaries import SceneBoundaryService
from tests.conftest import TestSessionLocal

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
VB_HASH = "1" * 64
VB_HASH_2 = "2" * 64
SCENE_SPEC_HASH = "3" * 64
SNAPSHOT_HASH = "4" * 64
PROMPT_HASH = "5" * 64
INPUT_HASH = "7" * 64

_IMAGE_BYTES = b"\x89PNG\r\n\x1a\nmock-image-bytes-" + b"x" * 32


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Chain + direct-row seeding helpers
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
    novel = Novel(title=f"Consistency Novel {username}", owner_id=user.id)
    db.add(novel)
    await db.flush()
    return user, novel


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
    novel = Novel(title="Consistency Novel testuser", owner_id=user.id)
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
    """Approved Visual Bible -> SceneSpec -> PromptRevision chain (fresh prompt)."""
    snapshot_hash = snapshot_hash or await _source_snapshot_hash(db, user.id, novel.id)
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


async def _persist_asset_row(
    db: AsyncSession,
    username: str,
    *,
    scene_spec_hash: str = SCENE_SPEC_HASH,
    user: User | None = None,
    novel: Novel | None = None,
) -> tuple[AssetRevision, IllustrationJob, User, Novel]:
    """Seed a candidate asset row (no provider call).

    When ``user``/``novel`` are provided the asset is created inside that
    scope; otherwise a fresh user/novel pair is created.
    """
    if user is None or novel is None:
        user, novel = await _seed_user_and_novel(db, username)
    else:
        user, novel = user, novel
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
        scene_spec_hash=scene_spec_hash,
        prompt_revision_id=101,
        prompt_revision_hash=PROMPT_HASH,
        visual_bible_revision_id=None,
        visual_bible_revision_hash=VB_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        cutoff_chapter=8,
        model_lineage={"provider": "mock", "model": "mock-img-v1"},
        config_hash="5" * 64,
        price_snapshot={},
        response_hash=None,
        schema_version="illustration.v1",
    )
    db.add(job)
    await db.flush()
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
        bytes_hash=hashlib.sha256(_IMAGE_BYTES).hexdigest(),
        scene_spec_hash=scene_spec_hash,
        prompt_revision_id=101,
        prompt_revision_hash=PROMPT_HASH,
        visual_bible_revision_hash=VB_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
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
    db.add(asset)
    await db.flush()
    return asset, job, user, novel


def _evidence_payload(**overrides) -> dict:
    payload = {
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
    }
    payload.update(overrides)
    return payload


def _runtime(
    tmp_path,
    *,
    mode: str = "success",
    policy: IllustrationBudgetPolicy = DEFAULT_ILLUSTRATION_POLICY,
):
    storage = AssetStorage(tmp_path / "assets")
    transport = MockIllustrationTransport(mode=mode)
    runtime = IllustrationWorkerRuntime(
        sessions=TestSessionLocal,
        gateway=IllustrationGateway(transport),
        storage=storage,
        budget_policy=policy,
    )
    return runtime, transport, storage


def _generation_request() -> IllustrationGenerationRequest:
    return IllustrationGenerationRequest.model_validate(
        {
            "prompt_revision_id": 1,
            "job_key": "job-arin-bamboo",
            "provider": "mock",
            "model": "mock-img-v1",
            "width": 1024,
            "height": 1024,
        }
    )


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
    """Seed the deterministic mock fixture registry and restore empty after."""
    set_illustration_consistency_fixtures(mock_consistency_fixture_registry())
    yield
    set_illustration_consistency_fixtures({})


def _evaluate_url(novel_id: int, asset_id: int) -> str:
    return (
        f"/api/novels/{novel_id}/illustrations/assets/{asset_id}/consistency/evaluate"
    )


# ---------------------------------------------------------------------------
# Full chain: generated candidate + consistency evaluation
# ---------------------------------------------------------------------------


async def test_consistency_evaluate_generated_asset_full_chain(
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

    eval_resp = await auth_client.post(
        _evaluate_url(novel.id, asset.id), json=_evidence_payload()
    )
    assert eval_resp.status_code == 201
    body = eval_resp.json()
    assert body["replayed"] is False
    report = body["report"]
    assert report["verdict"] == "pass"
    assert report["evaluator_id"] == "illustration-consistency.fixture.v1"
    assert report["reference_asset_ids"] == ["ref-char-arin-1", "ref-char-arin-2"]
    # The report freezes the exact source/prompt/model lineage of the asset.
    assert report["details"]["asset"]["scene_spec_hash"] == asset.scene_spec_hash
    assert (
        report["details"]["asset"]["source_snapshot_hash"] == asset.source_snapshot_hash
    )
    assert (
        report["details"]["asset"]["prompt_revision_hash"] == asset.prompt_revision_hash
    )
    assert report["details"]["asset"]["cutoff_chapter"] == asset.cutoff_chapter
    assert report["details"]["asset"]["model_lineage"] == asset.model_lineage

    # D-33-04: the score is evidence only — the asset stays a candidate.
    asset = await db_session.scalar(
        select(AssetRevision).where(AssetRevision.id == asset.id)
    )
    assert asset.approval_state == "candidate"
    assert asset.rights_status == "unreviewed"


# ---------------------------------------------------------------------------
# Verdicts + idempotent replay
# ---------------------------------------------------------------------------


async def test_consistency_verdicts_and_idempotent_replay(
    auth_client, db_session, consistency_fixtures_enabled
):
    user, novel = await _seed_testuser_novel(db_session)
    asset, _, _, _ = await _persist_asset_row(
        db_session, "ill_cons_api", user=user, novel=novel
    )
    await db_session.commit()

    # 1. Clean candidate -> pass, one persisted row.
    first = await auth_client.post(
        _evaluate_url(novel.id, asset.id), json=_evidence_payload()
    )
    assert first.status_code == 201
    assert first.json()["report"]["verdict"] == "pass"
    assert first.json()["replayed"] is False

    rows = (
        await db_session.scalars(
            select(ConsistencyReport).where(
                ConsistencyReport.asset_revision_id == asset.id
            )
        )
    ).all()
    assert len(rows) == 1

    # 2. Same report_key + same evidence replays the same row.
    again = await auth_client.post(
        _evaluate_url(novel.id, asset.id), json=_evidence_payload()
    )
    assert again.status_code == 201
    assert again.json()["replayed"] is True
    assert again.json()["report"]["id"] == first.json()["report"]["id"]
    assert (
        await db_session.scalar(
            select(ConsistencyReport).where(
                ConsistencyReport.asset_revision_id == asset.id
            )
        )
        is rows[0]
    )

    # 3. Identity drift -> concern (distinguishable evidence).
    drifted = await auth_client.post(
        _evaluate_url(novel.id, asset.id),
        json=_evidence_payload(
            report_key="arin:ch1-drifted",
            identity_attributes=["black_hair", "lean_build", "scar_left_brow"],
        ),
    )
    assert drifted.status_code == 201
    drifted_report = drifted.json()["report"]
    assert drifted_report["verdict"] == "concern"
    assert "amber_eyes" in drifted_report["details"]["drift"]["identity"]["missing"]

    # 4. Negative-constraint violation -> fail closed.
    violated = await auth_client.post(
        _evaluate_url(novel.id, asset.id),
        json=_evidence_payload(
            report_key="arin:ch1-violated",
            negative_constraints_present=["no_glasses"],
        ),
    )
    assert violated.status_code == 201
    violated_report = violated.json()["report"]
    assert violated_report["verdict"] == "fail"
    assert violated_report["details"]["drift"]["negative_constraints"]["violated"] == [
        "no_glasses"
    ]

    # 5. Same report_key with different evidence fails closed (409).
    conflict = await auth_client.post(
        _evaluate_url(novel.id, asset.id),
        json=_evidence_payload(identity_attributes=["black_hair"]),
    )
    assert conflict.status_code == 409


async def test_consistency_unavailable_without_evaluator(auth_client, db_session):
    user, novel = await _seed_testuser_novel(db_session)
    asset, _, _, _ = await _persist_asset_row(
        db_session, "ill_cons_unavail", user=user, novel=novel
    )
    await db_session.commit()

    resp = await auth_client.post(
        _evaluate_url(novel.id, asset.id), json=_evidence_payload()
    )
    assert resp.status_code == 201
    report = resp.json()["report"]
    assert report["verdict"] == "unavailable"
    assert report["details"]["reason_code"] == "fixture_missing"
    assert report["scores"] == {}
    assert report["reference_asset_ids"] == []


# ---------------------------------------------------------------------------
# Read-only compare / report API + owner scope
# ---------------------------------------------------------------------------


async def test_consistency_get_compare_and_list_api(
    auth_client, db_session, consistency_fixtures_enabled
):
    user, novel = await _seed_testuser_novel(db_session)
    asset, _, _, _ = await _persist_asset_row(
        db_session, "ill_cons_read", user=user, novel=novel
    )
    await db_session.commit()

    eval_resp = await auth_client.post(
        _evaluate_url(novel.id, asset.id), json=_evidence_payload()
    )
    assert eval_resp.status_code == 201
    report_id = eval_resp.json()["report"]["id"]

    detail = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/assets/{asset.id}/consistency"
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == report_id
    assert detail.json()["verdict"] == "pass"

    compare = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/assets/{asset.id}/consistency/compare"
    )
    assert compare.status_code == 200
    assert compare.json()["candidate"]["id"] == asset.id
    assert compare.json()["candidate"]["approval_state"] == "candidate"
    assert compare.json()["report"]["id"] == report_id

    listing = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/consistency-reports"
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == report_id

    # A foreign/absent asset's report is indistinguishable from not found.
    missing = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/assets/99999/consistency"
    )
    assert missing.status_code == 404


async def test_consistency_cross_owner_is_not_found(
    auth_client, db_session, consistency_fixtures_enabled
):
    await _demote_testuser(db_session)
    owner, novel = await _seed_user_and_novel(db_session, "other-owner")
    asset, _, _, _ = await _persist_asset_row(
        db_session, "ill_cons_xowner", user=owner, novel=novel
    )
    await db_session.commit()

    eval_resp = await auth_client.post(
        _evaluate_url(novel.id, asset.id), json=_evidence_payload()
    )
    assert eval_resp.status_code == 404
    listing = await auth_client.get(
        f"/api/novels/{novel.id}/illustrations/consistency-reports"
    )
    assert listing.status_code == 404
