"""Phase 38-01 derivative Visual Bible fork/lineage service tests (REQ-FORK-04).

Covers the fail-closed service gates on the real CI database:

- explicit fork from an Original Visual Bible snapshot: happy path persists a
  candidate derivative version with the source snapshot ref + divergence +
  provenance, and the Original rows are never touched;
- a foreign/missing project, a non-active project, a rejected/archived fork, a
  missing/foreign Original snapshot and a mutated source snapshot/manifest hash
  or cutoff all fail closed before any row is written;
- the sealed namespace rejects a non-``fanfiction_visual`` fork;
- the immutable version lineage rejects in-place source-hash mutation;
- review moves only the derivative review-state projection (approve/reject),
  is idempotent, and never touches the Original snapshot;
- two owners are isolated across the fork/read seams and an identical fork
  retry replays instead of duplicating.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.derivative_visual_assets import set_derivative_asset_storage
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.novel import Novel
from app.models.user import User
from app.schemas.derivative_visual import (
    DerivativeVisualReviewEventInput,
    DerivativeVisualState,
    DerivativeVisualVersionContract,
    recompute_derivative_visual_manifest_hash,
)
from app.schemas.derivative_visual import (
    DerivativeIdentityRow,
    DerivativeReferenceAssetRow,
    DerivativeSceneSpecContract,
    recompute_derivative_scene_spec_hash,
)
from app.schemas.derivative_visual_asset import (
    DerivativeAssetCandidateWrite,
    DerivativeAssetIdentityRow as CandidateIdentityRow,
    DerivativeAssetReviewEventInput as CandidateReviewEventInput,
    DerivativeAssetSourceRef,
    divergence_manifest_hash_from_spec,
)
from app.services.derivative_visual.assets import (
    DerivativeAssetStorage,
    DerivativeCandidateConflict,
    apply_derivative_asset_review,
    store_derivative_candidate_asset,
)
from app.services.derivative_visual.published_assets import (
    PublishedAssetNotFound,
    list_published_assets,
    load_published_asset,
)
from app.services.derivative_visual.fork import (
    DerivativeVisualForkError,
    create_derivative_visual_fork,
)
from app.services.derivative_visual.lineage import (
    DerivativeVisualVersionNotFoundError,
    apply_review,
    list_versions,
    load_version_view,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64


def _idem64() -> str:
    return uuid.uuid4().hex * 2


def async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest.fixture
async def db(migrated_postgres: str):
    aengine = create_async_engine(
        async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await aengine.dispose()


def _seed_owner(sync_url: str, *, suffix: str, fork_status: str = "approved") -> dict:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"dvi_{suffix}",
            email=f"dvi_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
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
            fork_key=f"ff-dvi-{suffix}",
            space="fanfiction_canon",
            status=fork_status,
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
            idempotency_key=_idem64(),
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
        "provenance": {"branch": "fork-1", "project_key": "proj"},
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


def _count_rows(sync_url: str, table: str, *, owner_id: int | None = None) -> int:
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        if owner_id is None:
            count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        else:
            count = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE owner_id = :oid"),
                {"oid": owner_id},
            ).scalar_one()
    engine.dispose()
    return count


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_fork_round_trip(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"ok_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-ok"),
    )
    await db.commit()

    assert result.replayed is False
    assert result.version.visual_namespace == "fanfiction_visual"
    assert result.version.source_version_id == ids["source_version_id"]
    assert result.version.source_snapshot_hash == HEX64
    assert result.version.source_manifest_hash == HEX64_C
    assert result.version.review_state == "candidate"
    assert result.entity_ids["char-arya"] > 0
    assert result.asset_ids["dv-arya"] > 0

    view = await load_version_view(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version_id=result.version.id,
    )
    assert view.namespace == "fanfiction_visual"
    assert view.divergence["style"] == "warm palette"
    assert view.review_state is DerivativeVisualState.CANDIDATE
    assert len(view.entities) == 1
    assert view.entities[0].source_entity_ref["source_entity_key"] == "char-arya"
    assert len(view.reference_assets) == 1
    assert view.reference_assets[0].approved is False

    listing = await list_versions(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"]
    )
    assert [item.version_key for item in listing] == ["dv-ok"]


async def test_identical_fork_retry_replays(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"replay_{uuid.uuid4().hex[:8]}")
    version = _fork_payload(ids, version_key="dv-replay")
    first = await create_derivative_visual_fork(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=version
    )
    await db.commit()
    second = await create_derivative_visual_fork(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=version
    )
    await db.commit()
    assert second.replayed is True
    assert second.version.id == first.version.id
    assert (
        _count_rows(
            migrated_postgres, "derivative_visual_versions", owner_id=ids["owner_id"]
        )
        == 1
    )


async def test_conflicting_fork_retry_fails_closed(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"conf_{uuid.uuid4().hex[:8]}")
    await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-conf"),
    )
    await db.commit()
    # Same version_key but different immutable content (changed divergence).
    payload = _fork_payload(
        ids, version_key="dv-conf", divergence={"style": "cold palette"}
    )
    with pytest.raises(DerivativeVisualForkError, match="fork_conflict"):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=payload
        )


# ---------------------------------------------------------------------------
# Gate failures (no rows written)
# ---------------------------------------------------------------------------


async def test_foreign_project_or_fork_is_rejected(migrated_postgres, db):
    a = _seed_owner(migrated_postgres, suffix=f"fp_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(migrated_postgres, suffix=f"fq_{uuid.uuid4().hex[:8]}")

    # Owner B's project referenced from A's scope.
    bad = _fork_payload(a, version_key="dv-foreign-project", project_id=b["project_id"])
    with pytest.raises(DerivativeVisualForkError, match="project_not_found"):
        await create_derivative_visual_fork(
            db, owner_id=a["owner_id"], novel_id=a["novel_id"], version=bad
        )

    # Owner B's fork referenced from A's scope.
    bad = _fork_payload(a, version_key="dv-foreign-fork", fork_id=b["fork_id"])
    with pytest.raises(DerivativeVisualForkError, match="fork_not_found"):
        await create_derivative_visual_fork(
            db, owner_id=a["owner_id"], novel_id=a["novel_id"], version=bad
        )

    # Owner B's Original snapshot referenced from A's scope.
    bad = _fork_payload(
        a, version_key="dv-foreign-source", source_version_id=b["source_version_id"]
    )
    with pytest.raises(DerivativeVisualForkError, match="source_version_not_found"):
        await create_derivative_visual_fork(
            db, owner_id=a["owner_id"], novel_id=a["novel_id"], version=bad
        )


async def test_rejected_or_archived_fork_cannot_anchor(migrated_postgres, db):
    ids = _seed_owner(
        migrated_postgres, suffix=f"rej_{uuid.uuid4().hex[:8]}", fork_status="archived"
    )
    with pytest.raises(DerivativeVisualForkError, match="fork_not_usable"):
        await create_derivative_visual_fork(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version=_fork_payload(ids, version_key="dv-archived-fork"),
        )


async def test_missing_or_foreign_source_snapshot_is_rejected(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"src_{uuid.uuid4().hex[:8]}")
    payload = _fork_payload(
        ids, version_key="dv-bad-source", source_version_id=999999999
    )
    with pytest.raises(DerivativeVisualForkError, match="source_version_not_found"):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=payload
        )


async def test_source_hash_mutation_fails_closed(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"hash_{uuid.uuid4().hex[:8]}")
    # Mutated source snapshot hash (original is HEX64).
    payload = _fork_payload(
        ids, version_key="dv-bad-hash", source_snapshot_hash=HEX64_B
    )
    with pytest.raises(
        DerivativeVisualForkError, match="source_snapshot_hash_mismatch"
    ):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=payload
        )
    # Mutated source manifest hash (original is HEX64_C).
    payload = _fork_payload(
        ids, version_key="dv-bad-manifest", source_manifest_hash=HEX64_B
    )
    with pytest.raises(
        DerivativeVisualForkError, match="source_manifest_hash_mismatch"
    ):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=payload
        )
    # Mutated cutoff.
    payload = _fork_payload(ids, version_key="dv-bad-cutoff", cutoff_chapter=12)
    with pytest.raises(DerivativeVisualForkError, match="cutoff_chapter_mismatch"):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=payload
        )
    assert (
        _count_rows(
            migrated_postgres, "derivative_visual_versions", owner_id=ids["owner_id"]
        )
        == 0
    )


async def test_wrong_namespace_is_rejected(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"ns_{uuid.uuid4().hex[:8]}")
    # The contract Literal rejects an Original namespace at the DTO boundary;
    # the service seals it again (defense-in-depth) with ``namespace_denied``.
    version = _fork_payload(ids, version_key="dv-ns").model_copy(
        update={"namespace": "original_canon"}
    )
    with pytest.raises(DerivativeVisualForkError, match="namespace_denied"):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=version
        )


async def test_empty_divergence_is_rejected(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"div_{uuid.uuid4().hex[:8]}")
    # Empty divergence fails the explicit-declaration gate (D-38-02).
    version = _fork_payload(ids, version_key="dv-div").model_copy(
        update={"divergence": {}}
    )
    with pytest.raises(DerivativeVisualForkError, match="divergence"):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=version
        )


# ---------------------------------------------------------------------------
# Immutability of the persisted fork
# ---------------------------------------------------------------------------


async def test_fork_lineage_is_immutable(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"imm_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-imm"),
    )
    await db.commit()

    version = await db.get(type(result.version), result.version.id)
    version.source_snapshot_hash = HEX64_B
    with pytest.raises(ValueError, match="immutable"):
        await db.flush()
    await db.rollback()

    # Original Visual Bible rows stay untouched.
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT source_snapshot_hash, manifest_hash FROM visual_bible_versions "
                    "WHERE id = :vid"
                ),
                {"vid": ids["source_version_id"]},
            )
            .mappings()
            .one()
        )
    engine.dispose()
    assert row["source_snapshot_hash"] == HEX64
    assert row["manifest_hash"] == HEX64_C


# ---------------------------------------------------------------------------
# Review (append-only, explicit, owner-scoped)
# ---------------------------------------------------------------------------


async def test_review_approve_and_idempotency(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"rv_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-review"),
    )
    await db.commit()

    event = DerivativeVisualReviewEventInput(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version_id=result.version.id,
        action="approve",
        actor_source="human",
        actor="editor",
        reason="matches the branch scene spec",
        event_key="ev-approve-1",
        from_review_state="candidate",
    )
    version = await apply_review(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
    )
    await db.commit()
    assert version.review_state == "approved"

    # Idempotent replay: same event_key, no second event, state unchanged.
    replay = await apply_review(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
    )
    await db.commit()
    assert replay.review_state == "approved"

    # A conflicting from_review_state fails closed.
    bad = DerivativeVisualReviewEventInput(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version_id=result.version.id,
        action="approve",
        actor_source="human",
        actor="editor",
        reason="again",
        event_key="ev-approve-2",
        from_review_state="candidate",  # current state is already approved
    )
    from app.services.derivative_visual.lineage import DerivativeVisualLineageError

    with pytest.raises(DerivativeVisualLineageError, match="from_review_state"):
        await apply_review(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=bad
        )


async def test_review_is_owner_scoped(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"scope_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-scope"),
    )
    await db.commit()

    event = DerivativeVisualReviewEventInput(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version_id=result.version.id,
        action="reject",
        actor_source="human",
        actor="editor",
        reason="wrong palette",
        event_key="ev-reject-1",
        from_review_state="candidate",
    )
    from app.services.derivative_visual.lineage import (
        DerivativeVisualScopeMismatchError,
    )

    # A foreign owner cannot review the version (event scope mismatch).
    with pytest.raises(DerivativeVisualScopeMismatchError):
        await apply_review(
            db, owner_id=ids["owner_id"] + 1000, novel_id=ids["novel_id"], event=event
        )
    # An unknown version inside the owner's scope is an identical not-found.
    with pytest.raises(DerivativeVisualVersionNotFoundError):
        await load_version_view(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            version_id=result.version.id + 999999,
        )


# ---------------------------------------------------------------------------
# Phase 38-03: derivative asset candidate storage + cross-chapter consistency
# ---------------------------------------------------------------------------

HEX64_D = "d" * 64


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
    evidence_chapter: int | None = None,
) -> dict:
    """A valid frozen canonical derivative Scene Spec bound to the fork version."""
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
        "provenance": {"branch": "fork-1", "project": "proj-1"},
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
                "evidence_key": f"ev-{spec_key}",
                "source_snapshot_id": "snap-1",
                "source_snapshot_hash": HEX64,
                "chapter_number": evidence_chapter or chapter_number,
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
    evidence_chapter: int | None = None,
) -> DerivativeSceneSpecContract:
    """Frozen canonical spec whose content_hash replays from its payload."""
    from app.schemas.derivative_visual import (
        DerivativeSceneSpecEvidenceRef,
    )

    payload = _spec_payload(
        ids,
        version,
        spec_key=spec_key,
        chapter_number=chapter_number,
        identity_source_hash=identity_source_hash,
        identity_divergence=identity_divergence,
        style_profile=style_profile,
        divergence=divergence,
        evidence_chapter=evidence_chapter,
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
    """Deterministic non-empty candidate bytes (matching the content hash)."""
    return bytes.fromhex("89504e470d0a1a0a0000000000000000")


def _content_hash(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


async def _approve_fork(db, ids, version) -> None:
    """Approve the derivative visual fork version (anchors candidate storage)."""
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
    from app.services.derivative_visual.lineage import apply_review as apply_fork_review

    result = await apply_fork_review(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
    )
    assert result.review_state == "approved"


async def test_candidate_store_round_trip(migrated_postgres, db, tmp_path):
    ids = _seed_owner(migrated_postgres, suffix=f"cv_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-cand"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()

    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    payload = _png_bytes()
    spec = _make_spec(ids, result.version, spec_key="ds-1", chapter_number=1)
    candidate = _candidate_write(
        spec, asset_key="cand-1", chapter_number=1, content_hash=_content_hash(payload)
    )
    row, replayed = await store_derivative_candidate_asset(
        db,
        storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        spec=spec,
        candidate=candidate,
        payload=payload,
    )
    await db.commit()
    assert replayed is False
    # Generated ID + allowlisted storage key + replayed checksum + lineage.
    assert row.asset_id.startswith("dv-")
    assert row.storage_key.startswith(
        f"derivative_assets/{ids['owner_id']}/{ids['novel_id']}/{result.version.id}/"
    )
    assert row.content_hash == _content_hash(payload)
    assert row.visual_namespace == "fanfiction_visual"
    assert row.scene_spec_hash == spec.content_hash
    assert row.identity_key == "char-arya"
    assert row.consistency_verdict == "unavailable"  # single chapter -> needs_review
    assert row.review_state == "needs_review"
    assert row.identity_lineage[0]["source_entity_hash"] == HEX64
    assert row.generator_lineage["provider"] == "mock"
    assert (
        storage.read(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            visual_version_id=result.version.id,
            asset_id=row.asset_id,
            mime_type="image/png",
        )
        == payload
    )

    # Original Visual Bible rows are never touched (REQ-FORK-04).
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    with engine.connect() as conn:
        orig = (
            conn.execute(
                text(
                    "SELECT source_snapshot_hash, manifest_hash FROM visual_bible_versions "
                    "WHERE id = :vid"
                ),
                {"vid": ids["source_version_id"]},
            )
            .mappings()
            .one()
        )
    engine.dispose()
    assert orig["source_snapshot_hash"] == HEX64
    assert orig["manifest_hash"] == HEX64_C


async def test_duplicate_candidate_replays_and_conflict_fails_closed(
    migrated_postgres, db, tmp_path
):
    ids = _seed_owner(migrated_postgres, suffix=f"dup_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-dup"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()

    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    payload = _png_bytes()
    spec = _make_spec(ids, result.version, spec_key="ds-1", chapter_number=1)
    candidate = _candidate_write(
        spec, asset_key="dup-1", chapter_number=1, content_hash=_content_hash(payload)
    )
    first, replayed = await store_derivative_candidate_asset(
        db,
        storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        spec=spec,
        candidate=candidate,
        payload=payload,
    )
    await db.commit()
    assert replayed is False

    # Identical retry replays; no second row.
    second, replayed = await store_derivative_candidate_asset(
        db,
        storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        spec=spec,
        candidate=candidate,
        payload=payload,
    )
    await db.commit()
    assert replayed is True
    assert second.id == first.id
    assert (
        _count_rows(
            migrated_postgres, "derivative_visual_candidates", owner_id=ids["owner_id"]
        )
        == 1
    )

    # Same asset_key with different content fails closed.
    other_payload = b"\x00" * 8
    bad = _candidate_write(
        spec,
        asset_key="dup-1",
        chapter_number=1,
        content_hash=_content_hash(other_payload),
    )
    with pytest.raises(
        DerivativeCandidateConflict, match="duplicate_candidate_conflict"
    ):
        await store_derivative_candidate_asset(
            db,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec,
            candidate=bad,
            payload=other_payload,
        )


async def test_checksum_and_spec_gates_fail_closed(migrated_postgres, db, tmp_path):
    ids = _seed_owner(migrated_postgres, suffix=f"cg_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-cg"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()

    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    payload = _png_bytes()
    spec = _make_spec(ids, result.version, spec_key="ds-1", chapter_number=1)

    # Claimed content hash does not replay from the bytes.
    bad = _candidate_write(
        spec,
        asset_key="cg-1",
        chapter_number=1,
        content_hash="0" * 64,
    )
    with pytest.raises(DerivativeCandidateConflict, match="content_hash_mismatch"):
        await store_derivative_candidate_asset(
            db,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec,
            candidate=bad,
            payload=payload,
        )

    # Candidate bound to a different scene spec hash.
    bad = _candidate_write(
        spec,
        asset_key="cg-2",
        chapter_number=1,
        content_hash=_content_hash(payload),
        scene_spec_hash="1" * 64,
    )
    with pytest.raises(DerivativeCandidateConflict, match="scene_spec_hash_mismatch"):
        await store_derivative_candidate_asset(
            db,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec,
            candidate=bad,
            payload=payload,
        )

    # Wrong divergence manifest hash.
    bad = _candidate_write(
        spec,
        asset_key="cg-3",
        chapter_number=1,
        content_hash=_content_hash(payload),
        divergence_manifest_hash="2" * 64,
    )
    with pytest.raises(
        DerivativeCandidateConflict, match="divergence_manifest_hash_mismatch"
    ):
        await store_derivative_candidate_asset(
            db,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec,
            candidate=bad,
            payload=payload,
        )

    # Identity lineage drift from the spec.
    bad = _candidate_write(
        spec,
        asset_key="cg-4",
        chapter_number=1,
        content_hash=_content_hash(payload),
        identity_source_hash="3" * 64,
    )
    with pytest.raises(DerivativeCandidateConflict, match="identity_lineage_mismatch"):
        await store_derivative_candidate_asset(
            db,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec,
            candidate=bad,
            payload=payload,
        )

    # A tampered spec (mutated divergence) fails its own replay gate.
    tampered = spec.model_copy(update={"divergence": {"style": "cold palette"}})
    with pytest.raises(DerivativeCandidateConflict, match="scene_spec_invalid"):
        await store_derivative_candidate_asset(
            db,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=tampered,
            candidate=_candidate_write(
                spec,
                asset_key="cg-5",
                chapter_number=1,
                content_hash=_content_hash(payload),
            ),
            payload=payload,
        )

    assert (
        _count_rows(
            migrated_postgres, "derivative_visual_candidates", owner_id=ids["owner_id"]
        )
        == 0
    )


async def test_unapproved_fork_cannot_anchor_candidates(
    migrated_postgres, db, tmp_path
):
    ids = _seed_owner(migrated_postgres, suffix=f"ua_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-ua"),
    )
    await db.commit()
    # fork stays candidate — no approval.

    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    payload = _png_bytes()
    spec = _make_spec(ids, result.version, spec_key="ds-1", chapter_number=1)
    candidate = _candidate_write(
        spec, asset_key="ua-1", chapter_number=1, content_hash=_content_hash(payload)
    )
    with pytest.raises(DerivativeCandidateConflict, match="visual_fork_not_approved"):
        await store_derivative_candidate_asset(
            db,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec,
            candidate=candidate,
            payload=payload,
        )


async def _store_chapter_candidates(
    db,
    tmp_path,
    ids,
    version,
    *,
    spec_overrides: list[dict],
):
    """Store one candidate per chapter spec; returns (rows, storage)."""
    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    rows = []
    for index, overrides in enumerate(spec_overrides):
        payload = bytes([index + 1]) * 8
        spec = _make_spec(ids, version, **overrides)
        candidate = _candidate_write(
            spec,
            asset_key=f"ch-{overrides['chapter_number']}",
            chapter_number=overrides["chapter_number"],
            content_hash=_content_hash(payload),
        )
        row, _ = await store_derivative_candidate_asset(
            db,
            storage,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            spec=spec,
            candidate=candidate,
            payload=payload,
        )
        rows.append(row)
        await db.commit()
    return rows, storage


async def test_cross_chapter_consistency_pass_and_publish(
    migrated_postgres, db, tmp_path
):
    ids = _seed_owner(migrated_postgres, suffix=f"cc_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-cc"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()

    rows, _ = await _store_chapter_candidates(
        db,
        tmp_path,
        ids,
        result.version,
        spec_overrides=[
            {"spec_key": "ds-1", "chapter_number": 1},
            {"spec_key": "ds-2", "chapter_number": 2},
            {"spec_key": "ds-3", "chapter_number": 3},
        ],
    )
    # Third chapter's report covers all three chapters -> deterministic pass.
    report = dict(rows[2].consistency_report)
    assert report["verdict"] == "pass"
    assert len(report["chapters"]) == 3
    assert all(
        chapter["identity_consistent"] and chapter["style_consistent"]
        for chapter in report["chapters"]
    )
    # First chapter alone was unavailable -> needs_review; later ones candidate.
    assert rows[0].review_state == "needs_review"
    assert rows[2].review_state == "candidate"

    # Approve all three; published query returns them (owner/project/fork scope).
    for row in rows:
        event = CandidateReviewEventInput(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            candidate_id=row.id,
            action="approve",
            actor_source="human",
            actor="editor",
            reason="consistent across chapters",
            event_key=f"ev-approve-{row.id}",
            from_review_state=row.review_state,
        )
        await apply_derivative_asset_review(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
        )
        await db.commit()

    published = await list_published_assets(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"]
    )
    assert [item.asset_key for item in published] == ["ch-1", "ch-2", "ch-3"]
    assert all(item.review.review_state.value == "approved" for item in published)
    assert published[0].content_hash == rows[0].content_hash
    assert published[0].visual_version.version_id == result.version.id
    assert published[0].source_snapshot.source_snapshot_hash == HEX64
    assert published[0].divergence_manifest_hash == rows[0].divergence_manifest_hash

    loaded = await load_published_asset(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        asset_id=rows[2].asset_id,
    )
    assert loaded.asset_id == rows[2].asset_id


async def test_identity_drift_blocks_publish(migrated_postgres, db, tmp_path):
    ids = _seed_owner(migrated_postgres, suffix=f"dr_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-dr"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()

    rows, _ = await _store_chapter_candidates(
        db,
        tmp_path,
        ids,
        result.version,
        spec_overrides=[
            {"spec_key": "ds-1", "chapter_number": 1},
            {
                "spec_key": "ds-2",
                "chapter_number": 2,
                "identity_source_hash": "e" * 64,  # drifted Original entity pin
            },
        ],
    )
    report = dict(rows[1].consistency_report)
    assert report["verdict"] == "fail"
    assert any("identity_drift" in reason for reason in report["reasons"])
    assert rows[1].review_state == "blocked"

    # A blocked candidate can never be approved and never publishes.
    event = CandidateReviewEventInput(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        candidate_id=rows[1].id,
        action="approve",
        actor_source="human",
        actor="editor",
        reason="ignore drift",
        event_key=f"ev-approve-{rows[1].id}",
        from_review_state="blocked",
    )
    from app.services.derivative_visual.assets import DerivativeAssetReviewError

    with pytest.raises(DerivativeAssetReviewError, match="illegal"):
        await apply_derivative_asset_review(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], event=event
        )
    with pytest.raises(PublishedAssetNotFound):
        await load_published_asset(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            asset_id=rows[1].asset_id,
        )


async def test_style_divergence_declared_is_needs_review(
    migrated_postgres, db, tmp_path
):
    ids = _seed_owner(migrated_postgres, suffix=f"sd_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-sd"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()

    rows, _ = await _store_chapter_candidates(
        db,
        tmp_path,
        ids,
        result.version,
        spec_overrides=[
            {"spec_key": "ds-1", "chapter_number": 1},
            {
                "spec_key": "ds-2",
                "chapter_number": 2,
                "style_profile": {"palette": "cold"},
                "divergence": {"style": "cold palette", "note": "declared"},
            },
        ],
    )
    report = dict(rows[1].consistency_report)
    assert report["verdict"] == "concern"
    assert any("style_divergence_declared" in reason for reason in report["reasons"])
    assert rows[1].review_state == "needs_review"
    # needs_review is a human-review gate: not published until an explicit
    # approval, and a new candidate can still enter the same identity group.
    with pytest.raises(PublishedAssetNotFound):
        await load_published_asset(
            db,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            asset_id=rows[1].asset_id,
        )


async def test_style_divergence_undeclared_is_blocked(migrated_postgres, db, tmp_path):
    ids = _seed_owner(migrated_postgres, suffix=f"su_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-su"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()

    rows, _ = await _store_chapter_candidates(
        db,
        tmp_path,
        ids,
        result.version,
        spec_overrides=[
            {"spec_key": "ds-1", "chapter_number": 1},
            {
                "spec_key": "ds-2",
                "chapter_number": 2,
                "style_profile": {"palette": "cold"},
                # No style/palette declaration anywhere -> hidden divergence.
                "divergence": {"note": "branch A"},
                "identity_divergence": {"note": "same entity"},
            },
        ],
    )
    report = dict(rows[1].consistency_report)
    assert report["verdict"] == "fail"
    assert any("style_divergence_undeclared" in reason for reason in report["reasons"])
    assert rows[1].review_state == "blocked"


async def test_review_reject_and_owner_isolation(migrated_postgres, db, tmp_path):
    a = _seed_owner(migrated_postgres, suffix=f"iso_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(migrated_postgres, suffix=f"isb_{uuid.uuid4().hex[:8]}")

    # Owner A: approve the fork, store + approve one published asset.
    result = await create_derivative_visual_fork(
        db,
        owner_id=a["owner_id"],
        novel_id=a["novel_id"],
        version=_fork_payload(a, version_key="dv-iso"),
    )
    await db.commit()
    await _approve_fork(db, a, result.version)
    await db.commit()
    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    payload = _png_bytes()
    spec = _make_spec(a, result.version, spec_key="ds-1", chapter_number=1)
    candidate = _candidate_write(
        spec, asset_key="iso-1", chapter_number=1, content_hash=_content_hash(payload)
    )
    row, _ = await store_derivative_candidate_asset(
        db,
        storage,
        owner_id=a["owner_id"],
        novel_id=a["novel_id"],
        spec=spec,
        candidate=candidate,
        payload=payload,
    )
    await db.commit()
    event = CandidateReviewEventInput(
        owner_id=a["owner_id"],
        novel_id=a["novel_id"],
        candidate_id=row.id,
        action="approve",
        actor_source="human",
        actor="editor",
        reason="ok",
        event_key=f"ev-approve-{row.id}",
        from_review_state=row.review_state,
    )
    await apply_derivative_asset_review(
        db, owner_id=a["owner_id"], novel_id=a["novel_id"], event=event
    )
    await db.commit()

    # Owner B cannot see A's published asset (identical 404) and sees nothing.
    with pytest.raises(PublishedAssetNotFound):
        await load_published_asset(
            db,
            owner_id=b["owner_id"],
            novel_id=b["novel_id"],
            asset_id=row.asset_id,
        )
    b_list = await list_published_assets(
        db, owner_id=b["owner_id"], novel_id=b["novel_id"]
    )
    assert b_list == []

    # Owner A's reject of an approved asset removes it from the published set.
    reject_event = CandidateReviewEventInput(
        owner_id=a["owner_id"],
        novel_id=a["novel_id"],
        candidate_id=row.id,
        action="reject",
        actor_source="human",
        actor="editor",
        reason="wrong palette",
        event_key=f"ev-reject-{row.id}",
        from_review_state="approved",
    )
    await apply_derivative_asset_review(
        db, owner_id=a["owner_id"], novel_id=a["novel_id"], event=reject_event
    )
    await db.commit()
    a_list = await list_published_assets(
        db, owner_id=a["owner_id"], novel_id=a["novel_id"]
    )
    assert a_list == []
    with pytest.raises(PublishedAssetNotFound):
        await load_published_asset(
            db,
            owner_id=a["owner_id"],
            novel_id=a["novel_id"],
            asset_id=row.asset_id,
        )


async def test_candidate_lineage_is_immutable(migrated_postgres, db, tmp_path):
    ids = _seed_owner(migrated_postgres, suffix=f"imm_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-imm3"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()
    storage = DerivativeAssetStorage(tmp_path / "derivative_assets")
    payload = _png_bytes()
    spec = _make_spec(ids, result.version, spec_key="ds-1", chapter_number=1)
    candidate = _candidate_write(
        spec, asset_key="imm-1", chapter_number=1, content_hash=_content_hash(payload)
    )
    row, _ = await store_derivative_candidate_asset(
        db,
        storage,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        spec=spec,
        candidate=candidate,
        payload=payload,
    )
    await db.commit()

    row.content_hash = "1" * 64
    with pytest.raises(ValueError, match="immutable"):
        await db.flush()
    await db.rollback()

    # Original Visual Bible rows stay untouched after all candidate writes.
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM visual_bible_versions WHERE id = :vid"),
            {"vid": ids["source_version_id"]},
        ).scalar_one()
    engine.dispose()
    assert count == 1


async def test_api_store_publish_and_bytes_smoke(migrated_postgres, db, tmp_path):
    """End-to-end: store a candidate, review it, and read it as published."""
    ids = _seed_owner(migrated_postgres, suffix=f"api_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        version=_fork_payload(ids, version_key="dv-api"),
    )
    await db.commit()
    await _approve_fork(db, ids, result.version)
    await db.commit()

    storage = DerivativeAssetStorage(tmp_path / "api_assets")
    set_derivative_asset_storage(storage)

    aengine = create_async_engine(async_url(migrated_postgres), poolclass=NullPool)
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token({"sub": str(ids["owner_id"])})
    headers = {"Authorization": f"Bearer {token}"}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = _png_bytes()
            spec = _make_spec(ids, result.version, spec_key="ds-1", chapter_number=1)
            candidate = _candidate_write(
                spec,
                asset_key="api-1",
                chapter_number=1,
                content_hash=_content_hash(payload),
            )
            body = {
                "spec": spec.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
                "payload_base64": base64.b64encode(payload).decode(),
            }
            resp = await client.post(
                f"/api/novels/{ids['novel_id']}/derivative-visual/assets",
                json=body,
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            data = resp.json()
            assert data["asset"]["namespace"] == "fanfiction_visual"
            assert data["asset"]["review"]["consistency_verdict"] == "unavailable"
            assert data["asset"]["approval"] == "needs_review"
            asset_id = data["asset"]["asset_id"]

            # An unapproved candidate is not published.
            resp = await client.get(
                f"/api/novels/{ids['novel_id']}/derivative-visual/assets",
                headers=headers,
            )
            assert resp.status_code == 200
            assert resp.json()["total"] == 0
            resp = await client.get(
                f"/api/novels/{ids['novel_id']}/derivative-visual/assets/{asset_id}",
                headers=headers,
            )
            assert resp.status_code == 404

            # Explicit review approve -> published + bytes readable.
            resp = await client.post(
                f"/api/novels/{ids['novel_id']}/derivative-visual/assets/{asset_id}/review",
                json={
                    "event_key": f"ev-api-{asset_id}",
                    "action": "approve",
                    "actor_source": "human",
                    "actor": "editor",
                    "reason": "smoke approve",
                    "from_review_state": "needs_review",
                },
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["asset"]["approval"] == "approved"

            resp = await client.get(
                f"/api/novels/{ids['novel_id']}/derivative-visual/assets/{asset_id}",
                headers=headers,
            )
            assert resp.status_code == 200
            assert resp.json()["approval"] == "approved"
            assert resp.json()["content_hash"] == _content_hash(payload)
            assert resp.json()["visual_version"]["version_id"] == result.version.id
            assert resp.json()["source_snapshot"]["source_snapshot_hash"] == HEX64

            resp = await client.get(
                f"/api/novels/{ids['novel_id']}/derivative-visual/assets/{asset_id}/bytes",
                headers=headers,
            )
            assert resp.status_code == 200
            assert resp.content == payload

            # The consistency envelope exposes the review lineage reasons.
            resp = await client.get(
                f"/api/novels/{ids['novel_id']}/derivative-visual/assets/{asset_id}/consistency",
                headers=headers,
            )
            assert resp.status_code == 200
            assert resp.json()["review"]["reasons"] == ["insufficient_chapters"]
    finally:
        app.dependency_overrides.clear()
        set_derivative_asset_storage(None)
        await aengine.dispose()
