"""Phase 38-05 integration test: SkillRun → ToolRun → BranchVisualBibleArtifact →
publish_derivative_visual approval → deterministic review seam publication.

Prove Phase 38 derivative visual capability (D-38-01..D-38-04 / REQ-FORK-04 +
REQ-AGENT-02/03/04/07) is consumed through the versioned
illustrate-derivative-scene Skill and that the Agent cannot bypass approval /
publication authority:

Positive chain:
  register (versioned manifest: 8-tool allowlist = 7 read +
  publish_derivative_visual action + empty write_permissions + action in
  approval_required_for) → accept run (owner/novel/branch + input_hash binding)
  → approved derivative Visual Bible fork version + frozen canonical Scene Spec
  → stored candidate asset (deterministic consistency signal) → stub loop calls
  the real facade action tool (publish_derivative_visual creates a pending
  ApprovalRequest bound to the candidate's frozen lineage) → finalize writes the
  candidate BranchVisualBibleArtifact (review_state=candidate) → user confirms →
  deterministic review seam (consume_publish_derivative_visual_approval →
  review_candidate_asset) moves the candidate to approved — the published asset
  becomes owner/project/fork-visible through published_assets.

Adversarial paths (all stable blocked/cancelled/rejected with zero authoritative
writes):
  unknown tool registration, cancellation, wrong owner / skill_version /
  input_hash lineage, schema drift (status non-candidate, review_state
  non-candidate approval bypass), wrong branch, blocked candidate (identity
  drift / undeclared divergence → candidate_not_approvable), wrong
  scene_spec_hash, candidate outside the owner/novel scope, forged/stale/
  pending/rejected/cancelled approval, wrong fork scope, wrong approval action,
  stale candidate revision and Original-authority mutation attempts.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models import Novel, User
from app.models.agent_runtime import (
    ApprovalRequest,
    Artifact,
    ArtifactRevision,
    SkillRegistry,
    SkillRun,
    SkillVersion,
)
from app.models.visual_bible import VisualBibleVersion
from app.schemas.agent_runtime import SkillVersionRegister
from app.schemas.derivative_visual import (
    DerivativeIdentityRow,
    DerivativeReferenceAssetRow,
    DerivativeSceneSpecContract,
    DerivativeSceneSpecEvidenceRef,
    DerivativeVisualReviewEventInput,
    DerivativeVisualVersionContract,
    recompute_derivative_scene_spec_hash,
    recompute_derivative_visual_manifest_hash,
)
from app.schemas.derivative_visual_asset import (
    DerivativeAssetCandidateWrite,
    DerivativeAssetIdentityRow as CandidateIdentityRow,
    DerivativeAssetSourceRef,
    divergence_manifest_hash_from_spec,
)
from app.services.agent_runtime.approvals import confirm, expire_request, reject
from app.services.agent_runtime.finalize import (
    ERROR_CODE_FAILED_VALIDATION,
    finalize_skill_run,
)
from app.services.agent_runtime.registry import (
    SkillContractError,
    canonical_input_hash,
    register_skill_version,
)
from app.services.agent_runtime.structured_output_integrity import (
    canonical_content_hash,
)
from app.services.agent_tools.errors import InvalidInputError
from app.services.agent_tools.facade import ToolFacade
from app.services.derivative_visual.agent_boundary import (
    PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION,
    consume_publish_derivative_visual_approval,
    derivative_visual_approval_payload_hash,
)
from app.services.derivative_visual.assets import (
    DerivativeAssetStorage,
    store_derivative_candidate_asset,
)
from app.services.derivative_visual.fork import create_derivative_visual_fork
from app.services.derivative_visual.lineage import apply_review
from app.services.derivative_visual.published_assets import (
    PublishedAssetNotFound,
    list_published_assets,
    load_published_asset,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64

# Phase 38 编排 allowlist：7 个只读域工具 + 1 个 action 工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    "publish_derivative_visual",
]
APPROVAL_ACTIONS = ["publish_derivative_visual"]

FORK_KEY_PREFIX = "ff-dv38"
EVIDENCE_KEY = "ev-ds-1"
BRANCH_VALUE = "deriv-branch"
FORK_VALUE = "fork-1"


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(
    *, novel_id: int, name: str, tools: list[str], **overrides: Any
) -> SkillVersionRegister:
    base: dict[str, Any] = {
        "novel_id": novel_id,
        "name": name,
        "version": "1.0.0",
        "allowed_tools": list(tools),
        "read_permissions": ["canon", "fanfiction_canon", "visual_bible", "scene_spec"],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "user_interpretation",
            "derivative:autosave",
            "derivative:direct_write",
            "derivative_visual:write",
            "approval_request",
            "review_service",
            "published_assets",
        ],
        "budget": {
            "max_calls": 40,
            "max_input_tokens": 40_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        # Phase 38：action 要求独立 Web ApprovalRequest（D-11/D-15）。
        "approval_required_for": list(APPROVAL_ACTIONS),
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "branch": {"type": ["string", "null"]},
                "fork": {"type": ["string", "null"]},
                "visual_fork_version_id": {"type": "integer"},
                "scene_spec_hash": {"type": "string"},
                "candidate_asset_id": {"type": "integer"},
                "source_snapshot_hash": {"type": "string"},
                "evidence_refs": {"type": "array"},
                "requested_action": {"type": "array"},
            },
            "required": [
                "novel_id",
                "visual_fork_version_id",
                "scene_spec_hash",
                "candidate_asset_id",
                "source_snapshot_hash",
                "evidence_refs",
                "requested_action",
            ],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "branch_visual_bible"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner(sync_url: str, *, suffix: str) -> dict:
    """Seed owner + novel + original chapters + CanonFork + project + original Visual Bible."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"dvi_{suffix}",
            email=f"dvi_{suffix}@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"DVI Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=3,
            word_count=3,
        )
        session.add(novel)
        session.flush()
        from app.models.canon_fork import CanonFork

        fork = CanonFork(
            owner_id=user.id,
            novel_id=novel.id,
            fork_key=f"{FORK_KEY_PREFIX}-{suffix}",
            space="fanfiction_canon",
            status="approved",
            source_version_key="original:1",
            source_snapshot_id="snap-1",
            source_snapshot_hash=HEX64,
            through_chapter=3,
            full_book_authorized=False,
            cutoff_snapshot_hash=HEX64,
            scope_hash=HEX64,
            manifest_hash=HEX64,
            citation_lineage=[],
            authorization={},
            active=False,
        )
        session.add(fork)
        session.flush()
        from app.models.derivative_project import DerivativeProject

        project = DerivativeProject(
            owner_id=user.id,
            novel_id=novel.id,
            fork_id=fork.id,
            project_key=f"proj-{suffix}",
            name="Visual Fork Project",
            status="active",
            space="fanfiction_canon",
            fork_key=fork.fork_key,
            source_version_key="original:1",
            source_snapshot_hash=HEX64,
            through_chapter=3,
            full_book_authorized=False,
            cutoff_snapshot_hash=HEX64,
            scope_hash=HEX64,
            manifest_hash=HEX64,
        )
        session.add(project)
        session.flush()
        from app.models.visual_bible import VisualBibleVersion

        original = VisualBibleVersion(
            owner_id=user.id,
            novel_id=novel.id,
            version_key=f"vb-original-{suffix}",
            revision_number=1,
            source_snapshot_id="snap-1",
            source_snapshot_hash=HEX64,
            cutoff_chapter=8,
            review_state="candidate",
            schema_version="visual-bible.v1",
            schema_hash=HEX64,
            policy_hash=HEX64_B,
            manifest_hash=HEX64_C,
            canonical_payload={},
            canonical_payload_hash=HEX64,
            idempotency_key=uuid.uuid4().hex * 2,
            projection_hash=HEX64,
        )
        session.add(original)
        session.flush()
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "fork_id": fork.id,
            "project_id": project.id,
            "source_version_id": original.id,
            "token": create_access_token({"sub": str(user.id)}),
        }
    engine.dispose()
    return data


def _fork_payload(
    ids: dict, *, version_key: str, **overrides
) -> DerivativeVisualVersionContract:
    payload = {
        "schema_version": "derivative-visual.v1",
        "namespace": "fanfiction_visual",
        "owner_id": ids["owner_id"],
        "novel_id": ids["novel_id"],
        "project_id": ids["project_id"],
        "fork_id": ids["fork_id"],
        "version_key": version_key,
        "revision_number": 1,
        "source_version_id": ids["source_version_id"],
        "source_snapshot_id": "snap-1",
        "source_snapshot_hash": HEX64,
        "source_manifest_hash": HEX64_C,
        "cutoff_chapter": 8,
        "divergence": {"style": "warm palette", "note": "branch A"},
        "provenance": {"branch": FORK_VALUE, "project_key": "proj"},
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "manifest_hash": "0" * 64,
        "entities": [
            {
                "stable_id": "char-arya",
                "entity_key": "char-arya",
                "entity_type": "character",
                "description": "grey-eyed archer",
                "authority": "canon_fact",
                "divergence": {"palette": "soft greys"},
                "source_entity_ref": {
                    "source_entity_id": 7,
                    "source_entity_key": "char-arya",
                    "source_entity_hash": HEX64,
                },
                "disclosure_cutoff": 8,
            }
        ],
        "reference_assets": [
            {
                "asset_key": "dv-arya",
                "asset_id": "dv-obj-1",
                "mime_type": "image/png",
                "bytes_hash": HEX64_B,
                "source_asset_ref": {
                    "source_asset_id": "obj-1",
                    "source_bytes_hash": HEX64_B,
                },
            }
        ],
    }
    payload.update(overrides)
    version = DerivativeVisualVersionContract.model_validate(payload)
    if "manifest_hash" not in overrides:
        version = version.model_copy(
            update={"manifest_hash": recompute_derivative_visual_manifest_hash(version)}
        )
    return version


def _spec_payload(
    ids: dict,
    version,
    *,
    spec_key: str,
    chapter_number: int,
    identity_source_hash: str = HEX64,
    identity_divergence: dict | None = None,
    style_profile: dict | None = None,
    divergence: dict | None = None,
) -> dict:
    payload = {
        "schema_version": "derivative-scene-spec.v1",
        "artifact_kind": "derivative_scene_spec",
        "owner_id": ids["owner_id"],
        "novel_id": ids["novel_id"],
        "project_id": ids["project_id"],
        "fork_id": ids["fork_id"],
        "visual_namespace": "fanfiction_visual",
        "spec_key": spec_key,
        "revision_number": 1,
        "visual_fork_version_id": version.id,
        "visual_fork_version_hash": version.canonical_payload_hash,
        "scene_spec_id": None,
        "scene_spec_hash": HEX64,
        "scene_candidate_hash": HEX64,
        "visual_bible_revision_id": ids["source_version_id"],
        "visual_bible_revision_hash": HEX64_C,
        "source_snapshot_id": "snap-1",
        "source_snapshot_hash": HEX64,
        "source_manifest_hash": HEX64_C,
        "cutoff_chapter": 8,
        "divergence": divergence or {"style": "warm palette", "note": "branch A"},
        "provenance": {"branch": FORK_VALUE, "project": "proj-1"},
        "identity": [
            {
                "stable_id": "char-arya",
                "entity_key": "char-arya",
                "entity_type": "character",
                "description": "grey-eyed archer",
                "authority": "canon_fact",
                "divergence": identity_divergence or {"palette": "soft greys"},
                "source_entity_ref": {
                    "source_entity_id": 7,
                    "source_entity_key": "char-arya",
                    "source_entity_hash": identity_source_hash,
                },
                "disclosure_cutoff": 8,
            }
        ],
        "style_profile": style_profile
        if style_profile is not None
        else {"palette": "warm"},
        "negative_constraints": [],
        "reference_assets": [
            {
                "asset_key": "dv-arya",
                "asset_id": "dv-obj-1",
                "mime_type": "image/png",
                "bytes_hash": HEX64_B,
                "rights_status": "unreviewed",
                "source_asset_ref": {
                    "source_asset_id": "obj-1",
                    "source_bytes_hash": HEX64_B,
                },
                "approved": False,
            }
        ],
        "asset_lineage": [],
        "anchors": [],
        "evidence_refs": [
            {
                "evidence_key": EVIDENCE_KEY,
                "source_snapshot_id": "snap-1",
                "source_snapshot_hash": HEX64,
                "chapter_number": chapter_number,
                "source_start": 10,
                "source_end": 40,
                "content_hash": HEX64_B,
                "cutoff_chapter": 8,
            }
        ],
        "uncertainties": [],
        "export_manifest_hash": None,
        "content_hash": "0" * 64,
        "review_state": "candidate",
    }
    return payload


def _make_spec(
    ids: dict,
    version,
    *,
    spec_key: str,
    chapter_number: int,
    identity_source_hash: str = HEX64,
    identity_divergence: dict | None = None,
    style_profile: dict | None = None,
    divergence: dict | None = None,
) -> DerivativeSceneSpecContract:
    payload = _spec_payload(
        ids,
        version,
        spec_key=spec_key,
        chapter_number=chapter_number,
        identity_source_hash=identity_source_hash,
        identity_divergence=identity_divergence,
        style_profile=style_profile,
        divergence=divergence,
    )
    draft = DerivativeSceneSpecContract.model_construct(
        identity=[
            DerivativeIdentityRow.model_validate(row) for row in payload["identity"]
        ],
        reference_assets=[
            DerivativeReferenceAssetRow.model_validate(row)
            for row in payload["reference_assets"]
        ],
        evidence_refs=[
            DerivativeSceneSpecEvidenceRef.model_validate(row)
            for row in payload["evidence_refs"]
        ],
        negative_constraints=[],
        asset_lineage=[],
        anchors=[],
        uncertainties=[],
        **{
            key: value
            for key, value in payload.items()
            if key
            not in {
                "identity",
                "reference_assets",
                "evidence_refs",
                "negative_constraints",
                "asset_lineage",
                "anchors",
                "uncertainties",
            }
        },
    )
    spec = draft.model_copy(
        update={"content_hash": recompute_derivative_scene_spec_hash(draft)}
    )
    return DerivativeSceneSpecContract.model_validate(spec.model_dump())


def _candidate_write(
    spec: DerivativeSceneSpecContract,
    *,
    asset_key: str,
    chapter_number: int,
    content_hash: str,
    identity_source_hash: str | None = None,
    scene_spec_hash: str | None = None,
    divergence_manifest_hash: str | None = None,
) -> DerivativeAssetCandidateWrite:
    identity = spec.identity[0]
    source_ref = spec.reference_assets[0]
    if identity_source_hash is None:
        identity_source_hash = str(identity.source_entity_ref["source_entity_hash"])
    return DerivativeAssetCandidateWrite(
        asset_key=asset_key,
        chapter_number=chapter_number,
        mime_type="image/png",
        content_hash=content_hash,
        scene_spec_hash=scene_spec_hash or spec.content_hash,
        divergence_manifest_hash=(
            divergence_manifest_hash or divergence_manifest_hash_from_spec(spec)
        ),
        identity_lineage=[
            CandidateIdentityRow(
                stable_id=identity.stable_id,
                entity_key=identity.entity_key,
                entity_type=identity.entity_type.value,
                source_entity_hash=identity_source_hash,
            )
        ],
        source_refs=[
            DerivativeAssetSourceRef(
                asset_key=source_ref.asset_key,
                asset_id=source_ref.asset_id,
                source_asset_id=source_ref.source_asset_ref["source_asset_id"],
                source_bytes_hash=source_ref.source_asset_ref["source_bytes_hash"],
            )
        ],
        generator_lineage={"provider": "mock", "provider_model": "mock-1"},
    )


def _png_bytes() -> bytes:
    return bytes.fromhex("89504e470d0a1a0a0000000000000000")


def _content_hash(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


# ────────────────────────── agent_runtime fixtures ──────────────────────────


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest_asyncio.fixture
async def runtime_factory(migrated_postgres: str):
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api_client(runtime_factory):
    """ASGI client bound to the module-migrated PostgreSQL (head incl. 38-05)."""

    async def override_get_db():
        async with runtime_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# ────────────────────────── runtime helpers ──────────────────────────


async def _register_skill(
    factory, *, owner_id: int, novel_id: int, contract: SkillVersionRegister
) -> int:
    async with factory() as session:
        _, version = await register_skill_version(
            session, owner_id=owner_id, novel_id=novel_id, contract=contract
        )
        await session.commit()
        return version.id


async def _create_run(
    factory,
    *,
    owner_id: int,
    novel_id: int,
    skill_version_id: int,
    input_hash: str,
    input_data: dict[str, Any],
    branch: str | None = None,
    cancel_requested: bool = False,
) -> int:
    async with factory() as session:
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=skill_version_id,
            status="running",
            branch=branch,
            input=input_data,
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot={"max_calls": 40},
            cancel_requested=cancel_requested,
        )
        session.add(run)
        await session.commit()
        return run.id


async def _finalize(
    factory,
    *,
    run_id: int,
    envelope: dict[str, Any],
    frozen_manifest: dict[str, Any] | None = None,
    stop_reason: str = "stop",
):
    return await finalize_skill_run(
        factory,
        run_id=run_id,
        stop_reason=stop_reason,
        envelope=envelope,
        model_lineage={
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        source_versions=dict(envelope.get("source_versions") or {}),
        usage={
            "calls": 4,
            "input_tokens": 600,
            "output_tokens": 300,
            "cost_usd": "0.0015",
        },
        frozen_manifest=frozen_manifest,
    )


async def _count(
    factory, model, *, run_id: int | None = None, owner_id: int | None = None
) -> int:
    async with factory() as session:
        if owner_id is not None:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.owner_id == owner_id)  # type: ignore[attr-defined]
                )
                or 0
            )
        if run_id is None:
            return int(
                await session.scalar(select(func.count()).select_from(model)) or 0
            )
        return int(
            await session.scalar(
                select(func.count()).select_from(model).where(model.run_id == run_id)  # type: ignore[attr-defined]
            )
            or 0
        )


async def _count_revisions(factory, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ArtifactRevision)
                .join(Artifact, ArtifactRevision.artifact_id == Artifact.id)
                .where(Artifact.run_id == run_id)
            )
            or 0
        )


async def _count_approvals(factory, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ApprovalRequest)
                .where(ApprovalRequest.run_id == run_id)
            )
            or 0
        )


async def _assert_zero_writes(factory, *, run_id: int) -> None:
    assert await _count(factory, Artifact, run_id=run_id) == 0
    assert await _count_revisions(factory, run_id=run_id) == 0
    assert await _count_approvals(factory, run_id=run_id) == 0


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _run_input(ids: dict[str, Any]) -> dict[str, Any]:
    return {
        "novel_id": ids["novel_id"],
        "branch": BRANCH_VALUE,
        "fork": FORK_VALUE,
        "visual_fork_version_id": ids["visual_version_id"],
        "scene_spec_hash": ids["scene_spec_hash"],
        "candidate_asset_id": ids["candidate_asset_id"],
        "source_snapshot_id": f"novel:{ids['novel_id']}:{FORK_VALUE}",
        "source_snapshot_hash": HEX64,
        "evidence_refs": [EVIDENCE_KEY],
        "requested_action": ["publish_derivative_visual"],
    }


def _build_envelope(ctx: dict[str, Any], *, mutate=None) -> dict[str, Any]:
    """Build a canonical BranchVisualBibleArtifact envelope from the persisted
    candidate row + run lineage.

    ``mutate`` (optional) is applied **before** the 26-06 normalization trail is
    computed, so an adversarial mutation keeps a replay-consistent trail and the
    failure lands on the intended gate rather than on ``repaired_hash``.
    """
    candidate = ctx["candidate"]
    revision: dict[str, Any] = {
        "schema_version": "branch-illustration-revision.v1",
        "artifact_kind": "branch_illustration_revision",
        "authority_space": "derivative",
        "fork": FORK_VALUE,
        "visual_version": {
            "version_id": ctx["visual_version_id"],
            "version_key": ctx["version_key"],
            "version_hash": ctx["visual_version_hash"],
        },
        "source_snapshot": {
            "source_snapshot_id": candidate.source_snapshot_id,
            "source_snapshot_hash": candidate.source_snapshot_hash,
            "source_manifest_hash": candidate.source_manifest_hash,
            "cutoff_chapter": candidate.cutoff_chapter,
        },
        "scene_spec_hash": candidate.scene_spec_hash,
        "candidate_asset": {
            "candidate_asset_id": candidate.id,
            "asset_id": candidate.asset_id,
            "asset_key": candidate.asset_key,
            "content_hash": candidate.content_hash,
            "mime_type": candidate.mime_type,
        },
        "identity_lineage": list(candidate.identity_lineage or []),
        "source_refs": list(candidate.source_refs or []),
        "generator_lineage": dict(candidate.generator_lineage or {}),
        "divergence_manifest_hash": candidate.divergence_manifest_hash,
        "consistency_verdict": candidate.consistency_verdict,
        "validator_report": dict(candidate.consistency_report or {}),
        "review_state": "candidate",
    }
    envelope: dict[str, Any] = {
        "type": "branch_visual_bible",
        "schema_version": "branch-visual-bible.v1",
        "owner_id": ctx["owner_id"],
        "novel_id": ctx["novel_id"],
        "branch": BRANCH_VALUE,
        "producing_skill": "illustrate-derivative-scene",
        "producing_skill_version": "1.0.0",
        "skill_version_id": ctx["skill_version_id"],
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {
            "novel": "v1",
            "source_snapshot_hash": candidate.source_snapshot_hash,
        },
        "input_hash": ctx["input_hash"],
        "evidence_refs": [EVIDENCE_KEY],
        "revision": revision,
        "tool_runs": [
            {"tool_name": "get_novel", "calls": 1},
            {"tool_name": "get_chapter", "calls": 1},
            {"tool_name": "publish_derivative_visual", "calls": 1},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    if mutate is not None:
        mutate(envelope)
    repaired_hash = canonical_content_hash(_strip_trail(envelope))
    envelope["normalization"] = {
        "raw_hash": repaired_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    return envelope


def _publish_params(ctx: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": BRANCH_VALUE,
        "fork": FORK_VALUE,
        "candidate_asset_id": ctx["candidate_asset_id"],
        "scene_spec_hash": ctx["scene_spec_hash"],
        "approval_note": "publish the branch visual revision",
        "run_id": ctx["run_id"],
        "skill_version_id": ctx["skill_version_id"],
    }
    base.update(overrides)
    return base


async def _approve_fork(db, ids, version) -> None:
    event = DerivativeVisualReviewEventInput(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version_id=version.id,
        action="approve",
        actor_source="human",
        actor="editor",
        reason="branch visual bible approved",
        event_key=f"ev-approve-{version.id}",
        from_review_state="candidate",
    )
    result = await apply_review(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
    )
    assert result.review_state == "approved"


async def _store_candidate(
    runtime_factory,
    tmp_path,
    ids: dict[str, Any],
    version,
    *,
    asset_key: str,
    spec,
    chapter_number: int = 1,
) -> Any:
    """Store one candidate asset through the deterministic 38-03 store gate."""
    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    payload = _png_bytes()
    candidate = _candidate_write(
        spec,
        asset_key=asset_key,
        chapter_number=chapter_number,
        content_hash=_content_hash(payload),
    )
    async with runtime_factory() as session:
        row, replayed = await store_derivative_candidate_asset(
            session,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec,
            candidate=candidate,
            payload=payload,
        )
        await session.commit()
    assert replayed is False
    return row


async def _set_up(
    runtime_factory,
    sync_url: str,
    *,
    suffix: str,
    tmp_path,
    identity_source_hash: str = HEX64,
) -> dict[str, Any]:
    """seed owner/novel + visual fork + approved fork version + frozen Scene Spec
    + stored candidate + skill + run."""
    ids = _seed_owner(sync_url, suffix=suffix)

    # 1. derivative Visual Bible fork version (38-01) + explicit review approve.
    async with runtime_factory() as session:
        result = await create_derivative_visual_fork(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version=_fork_payload(ids, version_key=f"dv-{suffix}"),
        )
        await session.commit()
        await _approve_fork(session, ids, result.version)
        await session.commit()
    version = result.version
    ids["visual_version_id"] = version.id
    ids["version_key"] = version.version_key
    ids["visual_version_hash"] = version.canonical_payload_hash

    # 2. frozen canonical derivative Scene Spec (38-02) bound to the fork version.
    spec = _make_spec(
        ids,
        version,
        spec_key=f"ds-{suffix}",
        chapter_number=1,
        identity_source_hash=identity_source_hash,
    )
    ids["scene_spec_hash"] = spec.content_hash

    # 3. stored candidate asset (38-03) -> consistency unavailable -> needs_review.
    row = await _store_candidate(
        runtime_factory,
        tmp_path,
        ids,
        version,
        asset_key=f"cand-{suffix}",
        spec=spec,
    )
    ids["candidate"] = row
    ids["candidate_asset_id"] = row.id
    assert row.review_state == "needs_review"
    assert row.consistency_verdict == "unavailable"

    # 4. register the versioned illustrate-derivative-scene skill.
    svid = await _register_skill(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        contract=_skill_contract(
            novel_id=ids["novel_id"],
            name="illustrate-derivative-scene",
            tools=DEFAULT_TOOLS,
        ),
    )

    # 5. accept the run.
    run_input = _run_input(ids)
    input_hash = canonical_input_hash(run_input)
    run_id = await _create_run(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        skill_version_id=svid,
        input_hash=input_hash,
        input_data=run_input,
        branch=BRANCH_VALUE,
    )
    ids.update(
        {
            "skill_version_id": svid,
            "run_id": run_id,
            "input_hash": input_hash,
            "run_input": run_input,
        }
    )
    return ids


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase38_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 illustrate-derivative-scene manifest 注册成功：8 工具 allowlist
    （7 只读 + publish_derivative_visual action）+ 零写权限 + approval_required_for
    恰为 publish_derivative_visual。"""
    seed = _seed_owner(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="illustrate-derivative-scene",
            tools=DEFAULT_TOOLS,
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "illustrate-derivative-scene"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "publish_derivative_visual" in version.allowed_tools
        assert version.write_permissions == []
        assert set(version.approval_required_for) == set(APPROVAL_ACTIONS)
        assert version.forbidden_spaces == [
            "canon:original",
            "user_interpretation",
            "derivative:autosave",
            "derivative:direct_write",
            "derivative_visual:write",
            "approval_request",
            "review_service",
            "published_assets",
        ]
        assert "canon" in version.read_permissions
        assert "fanfiction_canon" in version.read_permissions
        assert int(version.budget["max_calls"]) == 40


async def test_phase38_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具（publish_derivative_visual_directly）→ 注册拒绝，零 active 行。"""
    seed = _seed_owner(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="illustrate-derivative-scene",
        tools=list(DEFAULT_TOOLS) + ["publish_derivative_visual_directly"],
    )
    with pytest.raises(SkillContractError):
        await _register_skill(
            runtime_factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            contract=contract,
        )
    async with runtime_factory() as session:
        registry_count = await session.scalar(
            select(func.count())
            .select_from(SkillRegistry)
            .where(SkillRegistry.owner_id == seed["owner_id"])
        )
    assert int(registry_count or 0) == 0


# ────────────────────────── Task 2：candidate → approval → deterministic review seam ──────────────────────────


async def test_phase38_publish_approval_and_deterministic_review_publish(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """正向链：已存储 candidate → publish_derivative_visual approval（绑定候选
    冻结血缘）→ finalize candidate BranchVisualBibleArtifact → 用户确认 →
    确定性 review seam 物化 approved published asset。绝不写 Original Visual Bible。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"ok_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    run_id = ctx["run_id"]

    # stub agent loop：真实调用 publish_derivative_visual action 工具。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION
    assert tool_view["approval_status"] == "pending"
    assert tool_view["candidate_asset_id"] == ctx["candidate_asset_id"]
    assert tool_view["content_hash"] == ctx["candidate"].content_hash
    assert (
        tool_view["divergence_manifest_hash"]
        == ctx["candidate"].divergence_manifest_hash
    )
    assert tool_view["consistency_verdict"] == "unavailable"
    assert tool_view["review_state"] == "needs_review"
    approval_id = int(tool_view["approval_request_id"])
    approval_payload_hash = str(tool_view["approval_payload_hash"])
    assert approval_payload_hash == derivative_visual_approval_payload_hash(
        ctx["candidate"]
    )

    async with runtime_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
    assert approval is not None and approval.status == "pending"
    assert approval.action == PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION
    assert approval.fork_id == ctx["candidate"].fork_id
    assert approval.novel_id == ctx["novel_id"]
    assert approval.payload_hash == approval_payload_hash

    # finalize 写入 candidate BranchVisualBibleArtifact（review_state=candidate）。
    frozen_manifest = {"evidence_refs": [EVIDENCE_KEY]}
    envelope = _build_envelope(ctx)
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        frozen_manifest=frozen_manifest,
    )
    assert outcome.status == "completed", outcome.status_reason
    assert await _count(runtime_factory, Artifact, run_id=run_id) == 1
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
    assert artifact is not None and artifact.type == "branch_visual_bible"
    assert artifact.schema_version == "branch-visual-bible.v1"
    assert artifact.status == "candidate"
    content = revision.content
    assert (
        canonical_content_hash(_strip_trail(content))
        == content["normalization"]["repaired_hash"]
    )
    assert content["type"] == "branch_visual_bible"
    assert content["owner_id"] == ctx["owner_id"]
    assert content["input_hash"] == ctx["input_hash"]
    assert content["branch"] == BRANCH_VALUE
    assert content["revision"]["authority_space"] == "derivative"
    assert content["revision"]["fork"] == FORK_VALUE
    assert content["revision"]["review_state"] == "candidate"
    assert (
        content["revision"]["candidate_asset"]["candidate_asset_id"]
        == ctx["candidate_asset_id"]
    )
    assert (
        content["revision"]["divergence_manifest_hash"]
        == ctx["candidate"].divergence_manifest_hash
    )
    assert content["revision"]["consistency_verdict"] == "unavailable"

    # 用户 Web 确认 publish approval → 确定性 review seam 物化 approved published asset。
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        published = await consume_publish_derivative_visual_approval(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            candidate_asset_id=ctx["candidate_asset_id"],
            approval_id=approval_id,
            reason="publish the branch visual revision",
            actor_id=ctx["owner_id"],
        )
        await session.commit()
    assert published.review.review_state.value == "approved"
    assert published.asset_id == ctx["candidate"].asset_id
    assert published.content_hash == ctx["candidate"].content_hash
    assert (
        published.divergence_manifest_hash == ctx["candidate"].divergence_manifest_hash
    )
    assert published.fork_id == ctx["candidate"].fork_id

    # published query 对该 owner/project/fork 可见。
    async with runtime_factory() as session:
        rows = await list_published_assets(
            session, owner_id=ctx["owner_id"], novel_id=ctx["novel_id"]
        )
        loaded = await load_published_asset(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            asset_id=ctx["candidate"].asset_id,
        )
    assert [item.asset_key for item in rows] == [ctx["candidate"].asset_key]
    assert loaded.asset_id == ctx["candidate"].asset_id

    # Original 章节 + Original Visual Bible 零变更。
    async with runtime_factory() as session:
        original = await session.get(VisualBibleVersion, ctx["source_version_id"])
    assert original is not None
    assert original.source_snapshot_hash == HEX64
    assert original.manifest_hash == HEX64_C


async def test_phase38_http_action_route_wired(
    runtime_factory, migrated_postgres: str, api_client, tmp_path
):
    """HTTP 路由连通：POST /api/agent-tools/publish_derivative_visual 经
    require_owned_novel 注入 owner/novel 后创建 pending approval（candidate-only）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"http_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    headers = {"Authorization": f"Bearer {ctx['token']}"}
    resp = await api_client.post(
        "/api/agent-tools/publish_derivative_visual",
        params={"novel_id": ctx["novel_id"]},
        headers=headers,
        json=_publish_params(ctx),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_only"] is True
    assert body["approval_action"] == PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION
    assert body["approval_status"] == "pending"
    assert body["approval_payload_hash"] == derivative_visual_approval_payload_hash(
        ctx["candidate"]
    )


# ────────────────────────── 对抗路径（fail closed，零权威写入） ──────────────────────────


async def test_phase38_cancellation_no_write(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"cancel_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    run_id = await _create_run(
        runtime_factory,
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        input_data=ctx["run_input"],
        branch=BRANCH_VALUE,
        cancel_requested=True,
    )
    envelope = _build_envelope(ctx)
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase38_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """envelope owner 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"own_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    envelope = _build_envelope(
        ctx,
        mutate=lambda e: e.__setitem__("owner_id", ctx["owner_id"] + 999),
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase38_stale_input_hash_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"hash_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    envelope = _build_envelope(
        ctx,
        mutate=lambda e: e.__setitem__("input_hash", "9" * 64),
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase38_schema_drift_status_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """schema drift：BranchVisualBibleArtifact status 非 candidate（直接发布伪造）
    → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"drift_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    envelope = _build_envelope(
        ctx,
        mutate=lambda e: e.__setitem__("status", "published"),
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "status" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase38_approval_bypass_review_state_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """approval bypass：revision.review_state 非 candidate（approved 伪造）→
    blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"bypass_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )

    def _forged(e):
        e["revision"]["review_state"] = "approved"

    envelope = _build_envelope(ctx, mutate=_forged)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "review_state" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase38_wrong_branch_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """wrong branch：run 绑定 derivative 分支，envelope 声称别的分支（branch 血缘
    不符）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"br_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )

    def _wrong_branch(e):
        e["branch"] = "other-branch"
        e["revision"]["fork"] = "fork-other"

    envelope = _build_envelope(ctx, mutate=_wrong_branch)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "branch" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase38_blocked_candidate_action_fails_closed(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """validator failure（identity drift → consistency fail → blocked candidate）：
    publish_derivative_visual action → candidate_not_approvable，零 ApprovalRequest。"""
    ids = _seed_owner(migrated_postgres, suffix=f"blk_{uuid.uuid4().hex[:6]}")
    async with runtime_factory() as session:
        result = await create_derivative_visual_fork(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version=_fork_payload(ids, version_key=f"dv-blk-{uuid.uuid4().hex[:6]}"),
        )
        await session.commit()
        await _approve_fork(session, ids, result.version)
        await session.commit()
    version = result.version

    # 先存 chapter 1（unavailable），再用 drifted identity 存 chapter 2 → blocked。
    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    spec1 = _make_spec(
        ids, version, spec_key=f"ds1-{uuid.uuid4().hex[:6]}", chapter_number=1
    )
    payload1 = bytes([1]) * 8
    async with runtime_factory() as session:
        await store_derivative_candidate_asset(
            session,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec1,
            candidate=_candidate_write(
                spec1,
                asset_key=f"blk-1-{uuid.uuid4().hex[:6]}",
                chapter_number=1,
                content_hash=_content_hash(payload1),
            ),
            payload=payload1,
        )
        await session.commit()

    spec2 = _make_spec(
        ids,
        version,
        spec_key=f"ds2-{uuid.uuid4().hex[:6]}",
        chapter_number=2,
        identity_source_hash="e" * 64,  # drifted Original entity pin
    )
    payload2 = bytes([2]) * 8
    async with runtime_factory() as session:
        blocked_row, _ = await store_derivative_candidate_asset(
            session,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec2,
            candidate=_candidate_write(
                spec2,
                asset_key=f"blk-2-{uuid.uuid4().hex[:6]}",
                chapter_number=2,
                content_hash=_content_hash(payload2),
            ),
            payload=payload2,
        )
        await session.commit()
    assert blocked_row.review_state == "blocked"

    # blocked candidate 无法请求发布（candidate_not_approvable）。

    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ids["novel_id"])
            await ToolFacade().execute(
                "publish_derivative_visual",
                db=session,
                novel=novel,
                owner_id=ids["owner_id"],
                params={
                    "branch": BRANCH_VALUE,
                    "fork": FORK_VALUE,
                    "candidate_asset_id": blocked_row.id,
                    "scene_spec_hash": spec2.content_hash,
                },
            )
    assert "candidate_not_approvable" in str(exc.value)
    async with runtime_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.owner_id == ids["owner_id"])
        )
    assert int(count or 0) == 0


async def test_phase38_wrong_scene_spec_hash_action_fails_closed(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """wrong scene_spec_hash（候选血缘不重放）→ publish tool fail closed，零 approval。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"spec_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "publish_derivative_visual",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_publish_params(ctx, scene_spec_hash="9" * 64),
            )
    assert "scene_spec_hash" in str(exc.value)
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0


async def test_phase38_candidate_outside_novel_scope_fails_closed(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """wrong owner/novel scope：另一本小说路径调用该候选 → scope mismatch，零 approval。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"scope_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    # 用一个不存在的 novel（同一 owner 的假 novel id）→ load_candidate 404-equivalent。
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            from app.models.novel import Novel as _Novel

            fake_novel = _Novel(
                id=ctx["novel_id"] + 999_999,
                owner_id=ctx["owner_id"],
                title="fake",
                chapter_count=1,
                reading_progress={},
            )
            await ToolFacade().execute(
                "publish_derivative_visual",
                db=session,
                novel=fake_novel,
                owner_id=ctx["owner_id"],
                params=_publish_params(ctx),
            )
    assert "scope" in str(exc.value).lower()
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0


async def test_phase38_consume_pending_approval_fails(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """确定性 review seam 消费 pending approval（未确认）→ approval_not_approved，
    零权威写入。"""
    from app.services.derivative_visual.agent_boundary import (
        DerivativeVisualBoundaryError,
    )

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"pend_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    # 未确认就消费 → fail closed。
    with pytest.raises(DerivativeVisualBoundaryError) as exc:
        async with runtime_factory() as session:
            await consume_publish_derivative_visual_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                candidate_asset_id=ctx["candidate_asset_id"],
                approval_id=approval_id,
                reason="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "approval_not_approved"
    # 候选仍未发布。
    async with runtime_factory() as session:
        with pytest.raises(PublishedAssetNotFound):
            await load_published_asset(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                asset_id=ctx["candidate"].asset_id,
            )


async def test_phase38_forged_approval_hash_fails(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """伪造 approval：确认后篡改 payload_hash（hash 绑定漂移）→ 确定性 review
    seam fail closed，不发布。"""
    from app.services.derivative_visual.agent_boundary import (
        DerivativeVisualBoundaryError,
    )

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"forge_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        approval = await session.get(ApprovalRequest, approval_id)
        approval.payload_hash = "c" * 64  # 篡改重放哈希（伪造批准）
        await session.commit()

    with pytest.raises(DerivativeVisualBoundaryError) as exc:
        async with runtime_factory() as session:
            await consume_publish_derivative_visual_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                candidate_asset_id=ctx["candidate_asset_id"],
                approval_id=approval_id,
                reason="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "approval_hash_mismatch"
    async with runtime_factory() as session:
        with pytest.raises(PublishedAssetNotFound):
            await load_published_asset(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                asset_id=ctx["candidate"].asset_id,
            )


async def test_phase38_wrong_fork_scope_fails(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """wrong fork scope：确认后篡改 approval.fork_id → 确定性 review seam fail
    closed（fork_scope_mismatch），不发布。"""
    from app.services.derivative_visual.agent_boundary import (
        DerivativeVisualBoundaryError,
    )

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"fork_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        approval = await session.get(ApprovalRequest, approval_id)
        approval.fork_id = approval.fork_id + 999_999  # 篡改 fork scope（wrong fork）
        await session.commit()

    with pytest.raises(DerivativeVisualBoundaryError) as exc:
        async with runtime_factory() as session:
            await consume_publish_derivative_visual_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                candidate_asset_id=ctx["candidate_asset_id"],
                approval_id=approval_id,
                reason="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "fork_scope_mismatch"
    async with runtime_factory() as session:
        with pytest.raises(PublishedAssetNotFound):
            await load_published_asset(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                asset_id=ctx["candidate"].asset_id,
            )


async def test_phase38_rejected_approval_fails(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """approval 被拒绝（rejected）→ 确定性 review seam fail closed（不发布）。"""
    from app.services.derivative_visual.agent_boundary import (
        DerivativeVisualBoundaryError,
    )

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"rej_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    async with runtime_factory() as session:
        await reject(session, request_id=approval_id, owner_id=ctx["owner_id"])
        await session.commit()

    with pytest.raises(DerivativeVisualBoundaryError) as exc:
        async with runtime_factory() as session:
            await consume_publish_derivative_visual_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                candidate_asset_id=ctx["candidate_asset_id"],
                approval_id=approval_id,
                reason="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "approval_not_approved"
    async with runtime_factory() as session:
        with pytest.raises(PublishedAssetNotFound):
            await load_published_asset(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                asset_id=ctx["candidate"].asset_id,
            )


async def test_phase38_cancelled_approval_fails(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """取消（run 结束/超时主动 expire）→ approval expired，确定性 review seam
    fail closed（不发布）。"""
    from app.services.derivative_visual.agent_boundary import (
        DerivativeVisualBoundaryError,
    )

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"exp_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    async with runtime_factory() as session:
        await expire_request(session, request_id=approval_id, owner_id=ctx["owner_id"])
        await session.commit()

    with pytest.raises(DerivativeVisualBoundaryError) as exc:
        async with runtime_factory() as session:
            await consume_publish_derivative_visual_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                candidate_asset_id=ctx["candidate_asset_id"],
                approval_id=approval_id,
                reason="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "approval_not_approved"
    async with runtime_factory() as session:
        with pytest.raises(PublishedAssetNotFound):
            await load_published_asset(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                asset_id=ctx["candidate"].asset_id,
            )


async def test_phase38_wrong_action_approval_fails(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """wrong approval action：把另一 action 的 approved approval 当 publish
    approval 消费 → approval_not_found（不发布）。"""
    from app.models.agent_runtime import ApprovalRequest as AR
    from app.services.derivative_visual.agent_boundary import (
        DerivativeVisualBoundaryError,
    )

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"act_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    # 伪造一个已批准的其它 action approval（例如 publish_illustration）。
    async with runtime_factory() as session:
        forged = AR(
            owner_id=ctx["owner_id"],
            run_id=ctx["run_id"],
            novel_id=ctx["novel_id"],
            branch_id=None,
            fork_id=ctx["candidate"].fork_id,
            action="publish_illustration",
            payload_summary={},
            payload_hash="d" * 64,
            status="approved",
        )
        session.add(forged)
        await session.commit()
        forged_id = forged.id

    with pytest.raises(DerivativeVisualBoundaryError) as exc:
        async with runtime_factory() as session:
            await consume_publish_derivative_visual_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                candidate_asset_id=ctx["candidate_asset_id"],
                approval_id=forged_id,
                reason="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "approval_not_found"
    async with runtime_factory() as session:
        with pytest.raises(PublishedAssetNotFound):
            await load_published_asset(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                asset_id=ctx["candidate"].asset_id,
            )


async def test_phase38_stale_candidate_revision_blocks_publish(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """stale candidate revision：候选已被批准（published）后再次请求发布 →
    candidate_not_approvable（stale revision fail closed）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"stale_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    # 走完整正向链发布一次。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await consume_publish_derivative_visual_approval(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            candidate_asset_id=ctx["candidate_asset_id"],
            approval_id=approval_id,
            reason="publish",
            actor_id=ctx["owner_id"],
        )
        await session.commit()

    # 已 published 的候选是 stale revision：再次请求发布 → fail closed。
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "publish_derivative_visual",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_publish_params(ctx),
            )
    assert "candidate_not_approvable" in str(exc.value)


async def test_phase38_action_is_idempotent(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """publish_derivative_visual 幂等：重复候选 + 相同血缘 → 重放既有 approval
    （一个 pending approval）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"idem_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        first = await facade.execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx),
        )
        second = await facade.execute(
            "publish_derivative_visual",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx, approval_note="replayed"),
        )
        await session.commit()
    assert first["approval_request_id"] == second["approval_request_id"]
    assert second["replayed"] is True
    async with runtime_factory() as session:
        approvals = (
            await session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.owner_id == ctx["owner_id"],
                    ApprovalRequest.action == PUBLISH_DERIVATIVE_VISUAL_APPROVAL_ACTION,
                )
            )
        ).all()
    assert len(approvals) == 1


async def test_phase38_original_authority_untouched(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """Original 权威零变更：Original Visual Bible 行不变；run 本身不创建任何
    ApprovalRequest（只有 action 工具创建）；无 published asset 越权出现。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"orig_{uuid.uuid4().hex[:6]}",
        tmp_path=tmp_path,
    )
    async with runtime_factory() as session:
        approvals_for_run = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.run_id == ctx["run_id"])
        )
        original = await session.get(VisualBibleVersion, ctx["source_version_id"])
    assert int(approvals_for_run or 0) == 0
    assert original is not None
    assert original.source_snapshot_hash == HEX64
    assert original.manifest_hash == HEX64_C
    # 未批准的 candidate 不在 published set 中（Original 侧无新增 published 资产）。
    async with runtime_factory() as session:
        with pytest.raises(PublishedAssetNotFound):
            await load_published_asset(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                asset_id=ctx["candidate"].asset_id,
            )
