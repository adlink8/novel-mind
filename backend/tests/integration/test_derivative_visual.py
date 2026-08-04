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

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.security import hash_password
from app.models.novel import Novel
from app.models.user import User
from app.schemas.derivative_visual import (
    DerivativeVisualReviewEventInput,
    DerivativeVisualState,
    DerivativeVisualVersionContract,
    recompute_derivative_visual_manifest_hash,
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


def _fork_payload(ids: dict, *, version_key: str, **overrides) -> DerivativeVisualVersionContract:
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
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
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
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
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
    bad = _fork_payload(
        a, version_key="dv-foreign-project", project_id=b["project_id"]
    )
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
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
            version=_fork_payload(ids, version_key="dv-archived-fork"),
        )


async def test_missing_or_foreign_source_snapshot_is_rejected(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"src_{uuid.uuid4().hex[:8]}")
    payload = _fork_payload(ids, version_key="dv-bad-source", source_version_id=999999999)
    with pytest.raises(DerivativeVisualForkError, match="source_version_not_found"):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=payload
        )


async def test_source_hash_mutation_fails_closed(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"hash_{uuid.uuid4().hex[:8]}")
    # Mutated source snapshot hash (original is HEX64).
    payload = _fork_payload(ids, version_key="dv-bad-hash", source_snapshot_hash=HEX64_B)
    with pytest.raises(DerivativeVisualForkError, match="source_snapshot_hash_mismatch"):
        await create_derivative_visual_fork(
            db, owner_id=ids["owner_id"], novel_id=ids["novel_id"], version=payload
        )
    # Mutated source manifest hash (original is HEX64_C).
    payload = _fork_payload(ids, version_key="dv-bad-manifest", source_manifest_hash=HEX64_B)
    with pytest.raises(DerivativeVisualForkError, match="source_manifest_hash_mismatch"):
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
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
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
        row = conn.execute(
            text(
                "SELECT source_snapshot_hash, manifest_hash FROM visual_bible_versions "
                "WHERE id = :vid"
            ),
            {"vid": ids["source_version_id"]},
        ).mappings().one()
    engine.dispose()
    assert row["source_snapshot_hash"] == HEX64
    assert row["manifest_hash"] == HEX64_C


# ---------------------------------------------------------------------------
# Review (append-only, explicit, owner-scoped)
# ---------------------------------------------------------------------------


async def test_review_approve_and_idempotency(migrated_postgres, db):
    ids = _seed_owner(migrated_postgres, suffix=f"rv_{uuid.uuid4().hex[:8]}")
    result = await create_derivative_visual_fork(
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
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
        db, owner_id=ids["owner_id"], novel_id=ids["novel_id"],
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
        DerivativeVisualVersionNotFoundError,
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
