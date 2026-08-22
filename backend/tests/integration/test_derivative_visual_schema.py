"""Phase 38-01 derivative Visual Bible schema/migration PostgreSQL tests.

REQ-FORK-04 / D-38-01 / D-38-02 against the real CI database:

- the four derivative Visual Bible tables exist after ``upgrade head`` and the
  migration round-trips (downgrade drops them, upgrade recreates them);
- the sealed namespace check constraint rejects an Original Canon namespace at
  the database level;
- the composite RESTRICT source FK prevents deleting an Original Visual Bible
  snapshot while a derivative version references it (Original rows immutable);
- hash length / review-state check constraints are enforced at the DB level;
- two owners are isolated: a derivative version cannot reference another
  owner's Original snapshot or project.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.security import hash_password
from app.models.novel import Chapter, Novel
from app.models.user import User
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64


def _idem64() -> str:
    return uuid.uuid4().hex * 2


VERSIONS = "derivative_visual_versions"
TABLES = {
    VERSIONS,
    "derivative_visual_entities",
    "derivative_visual_assets",
    "derivative_visual_review_events",
}


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


def _seed_owner(sync_url: str, *, suffix: str) -> dict:
    """Seed user + novel + fork + project + Original Visual Bible snapshot."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"dvs_{suffix}",
            email=f"dvs_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"DVS Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=3,
            word_count=3,
        )
        session.add(novel)
        session.flush()
        for i in range(1, 4):
            session.add(
                Chapter(
                    novel_id=novel.id,
                    chapter_number=i,
                    title=f"C{i}",
                    content=f"chapter {i} body",
                    word_count=13,
                )
            )
        # Fanfiction Canon fork (usable anchor for the project).
        fork = _insert_fork(
            session, owner_id=user.id, novel_id=novel.id, fork_key=f"ff-{suffix}"
        )
        session.flush()
        project = _insert_project(
            session, owner_id=user.id, novel_id=novel.id, fork_id=fork.id
        )
        session.flush()
        original = _insert_original_visual_bible(
            session, owner_id=user.id, novel_id=novel.id
        )
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "fork_id": fork.id,
            "project_id": project.id,
            "source_version_id": original,
            "source_snapshot_hash": HEX64,
            "source_manifest_hash": HEX64_C,
            "cutoff_chapter": 8,
        }
    engine.dispose()
    return data


def _insert_fork(session, *, owner_id, novel_id, fork_key):
    from app.models.canon_fork import CanonFork

    row = CanonFork(
        owner_id=owner_id,
        novel_id=novel_id,
        fork_key=fork_key,
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
    session.add(row)
    session.flush()
    return row


def _insert_project(session, *, owner_id, novel_id, fork_id):
    from app.models.derivative_project import DerivativeProject

    row = DerivativeProject(
        owner_id=owner_id,
        novel_id=novel_id,
        fork_id=fork_id,
        project_key=f"proj-{uuid.uuid4().hex[:8]}",
        name="Visual Fork Project",
        status="active",
        space="fanfiction_canon",
        fork_key="ff-x",
        source_version_key="original:1",
        source_snapshot_hash=HEX64,
        through_chapter=3,
        full_book_authorized=False,
        cutoff_snapshot_hash=HEX64,
        scope_hash=HEX64,
        manifest_hash=HEX64,
    )
    session.add(row)
    session.flush()
    return row


def _insert_original_visual_bible(session, *, owner_id, novel_id):
    from app.models.visual_bible import VisualBibleVersion

    row = VisualBibleVersion(
        owner_id=owner_id,
        novel_id=novel_id,
        version_key=f"vb-original-{uuid.uuid4().hex[:8]}",
        revision_number=1,
        parent_version_id=None,
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
    session.add(row)
    session.flush()
    return row.id


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------


def test_tables_exist_after_upgrade(migrated_postgres):
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    with engine.connect() as conn:
        present = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars()
        )
        assert TABLES <= present
    engine.dispose()


def test_migration_round_trip_downgrade_upgrade(migrated_postgres):
    # Downgrade to the pre-Phase-38 head (20260802_derivative_override01) so the
    # whole derivative Visual Bible surface (versions/entities/assets/review
    # events, created by 38_derivative_visual01) is dropped; ``-1`` would only
    # roll back the 38-03 asset candidate migration (2 tables).
    run_alembic(
        "downgrade", "20260802_derivative_override01", database_url=migrated_postgres
    )
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            present = set(
                conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                ).scalars()
            )
            assert not TABLES & present
    finally:
        engine.dispose()
    run_alembic("upgrade", "head", database_url=migrated_postgres)
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    with engine.connect() as conn:
        present = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            ).scalars()
        )
        assert TABLES <= present
    engine.dispose()


def test_database_rejects_wrong_namespace(migrated_postgres):
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    ids = _seed_owner(migrated_postgres, suffix=f"ns_{uuid.uuid4().hex[:8]}")
    with engine.connect() as conn:
        try:
            _insert_derivative_version_raw(
                conn, ids=ids, version_key="dv-ns", namespace="original_canon"
            )
        except IntegrityError as exc:
            assert "ck_derivative_visual_versions_namespace" in str(exc)
        else:
            pytest.fail("original_canon derivative row must be rejected by the DB")
        finally:
            conn.rollback()
    engine.dispose()


def _insert_derivative_version_raw(conn, *, ids, version_key, namespace):
    conn.execute(
        text(
            f"INSERT INTO {VERSIONS} (owner_id, novel_id, project_id, fork_id,"
            " visual_namespace, version_key, revision_number, source_version_id,"
            " source_snapshot_id, source_snapshot_hash, source_manifest_hash,"
            " cutoff_chapter, divergence, provenance, review_state, schema_version,"
            " schema_hash, policy_hash, manifest_hash, canonical_payload,"
            " canonical_payload_hash, idempotency_key, projection_hash)"
            " VALUES (:owner_id, :novel_id, :project_id, :fork_id, :namespace,"
            " :version_key, 1, :source_version_id, 'snap-1', :snap, :manifest, 8,"
            ' \'{"style":"warm"}\', \'{"branch":"fork-1"}\', \'candidate\','
            " 'derivative-visual.v1', :h1, :h2, :manifest, '{}'::jsonb, :h1, :idem, :h4)"
        ),
        {
            "owner_id": ids["owner_id"],
            "novel_id": ids["novel_id"],
            "project_id": ids["project_id"],
            "fork_id": ids["fork_id"],
            "namespace": namespace,
            "version_key": version_key,
            "source_version_id": ids["source_version_id"],
            "snap": HEX64,
            "manifest": HEX64_C,
            "h1": HEX64,
            "h2": HEX64_B,
            "idem": _idem64(),
            "h4": HEX64,
        },
    )


def test_original_snapshot_cannot_be_deleted_while_referenced(migrated_postgres):
    """The composite RESTRICT FK keeps Original Visual Bible rows immutable."""
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    ids = _seed_owner(migrated_postgres, suffix=f"del_{uuid.uuid4().hex[:8]}")
    with engine.connect() as conn:
        _insert_derivative_version_raw(
            conn, ids=ids, version_key="dv-del", namespace="fanfiction_visual"
        )
        conn.commit()
        try:
            conn.execute(
                text("DELETE FROM visual_bible_versions WHERE id = :vid"),
                {"vid": ids["source_version_id"]},
            )
        except IntegrityError as exc:
            assert "fk_derivative_visual_versions_source_scope" in str(exc)
        else:
            pytest.fail(
                "deleting a referenced Original Visual Bible snapshot must fail"
            )
        finally:
            conn.rollback()
    engine.dispose()


def test_original_rows_stay_unchanged_by_derivative_write(migrated_postgres):
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    ids = _seed_owner(migrated_postgres, suffix=f"mut_{uuid.uuid4().hex[:8]}")
    with engine.connect() as conn:
        before = dict(
            conn.execute(
                text(
                    "SELECT source_snapshot_hash, manifest_hash, review_state FROM "
                    "visual_bible_versions WHERE id = :vid"
                ),
                {"vid": ids["source_version_id"]},
            )
            .mappings()
            .one()
        )
        _insert_derivative_version_raw(
            conn, ids=ids, version_key="dv-mut", namespace="fanfiction_visual"
        )
        conn.commit()
        after = dict(
            conn.execute(
                text(
                    "SELECT source_snapshot_hash, manifest_hash, review_state FROM "
                    "visual_bible_versions WHERE id = :vid"
                ),
                {"vid": ids["source_version_id"]},
            )
            .mappings()
            .one()
        )
        assert before == after
    engine.dispose()


def test_review_state_and_hash_constraints_are_enforced(migrated_postgres):
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    ids = _seed_owner(migrated_postgres, suffix=f"ck_{uuid.uuid4().hex[:8]}")
    with engine.connect() as conn:
        try:
            conn.execute(
                text(
                    f"INSERT INTO {VERSIONS} (owner_id, novel_id, project_id, fork_id,"
                    " visual_namespace, version_key, revision_number, source_version_id,"
                    " source_snapshot_id, source_snapshot_hash, source_manifest_hash,"
                    " cutoff_chapter, divergence, provenance, review_state, schema_version,"
                    " schema_hash, policy_hash, manifest_hash, canonical_payload,"
                    " canonical_payload_hash, idempotency_key, projection_hash)"
                    " VALUES (:owner_id, :novel_id, :project_id, :fork_id,"
                    " 'fanfiction_visual', 'dv-bad-state', 1, :source_version_id, 'snap-1',"
                    " :h, :h, 8, '{}'::jsonb, '{}'::jsonb, 'published', 'derivative-visual.v1',"
                    " :h, :h, :h, '{}'::jsonb, :h, :idem, :h)"
                ),
                {
                    "owner_id": ids["owner_id"],
                    "novel_id": ids["novel_id"],
                    "project_id": ids["project_id"],
                    "fork_id": ids["fork_id"],
                    "source_version_id": ids["source_version_id"],
                    "h": HEX64,
                    "idem": _idem64(),
                },
            )
        except IntegrityError as exc:
            assert "ck_derivative_visual_versions_review_state" in str(exc)
        else:
            pytest.fail("a non-candidate/non-closed review_state must be rejected")
        finally:
            conn.rollback()

        try:
            conn.execute(
                text(
                    f"INSERT INTO {VERSIONS} (owner_id, novel_id, project_id, fork_id,"
                    " visual_namespace, version_key, revision_number, source_version_id,"
                    " source_snapshot_id, source_snapshot_hash, source_manifest_hash,"
                    " cutoff_chapter, divergence, provenance, review_state, schema_version,"
                    " schema_hash, policy_hash, manifest_hash, canonical_payload,"
                    " canonical_payload_hash, idempotency_key, projection_hash)"
                    " VALUES (:owner_id, :novel_id, :project_id, :fork_id,"
                    " 'fanfiction_visual', 'dv-short-hash', 1, :source_version_id, 'snap-1',"
                    " 'short', :h, 8, '{}'::jsonb, '{}'::jsonb, 'candidate',"
                    " 'derivative-visual.v1', :h, :h, :h, '{}'::jsonb, :h, :idem, :h)"
                ),
                {
                    "owner_id": ids["owner_id"],
                    "novel_id": ids["novel_id"],
                    "project_id": ids["project_id"],
                    "fork_id": ids["fork_id"],
                    "source_version_id": ids["source_version_id"],
                    "h": HEX64,
                    "idem": _idem64(),
                },
            )
        except IntegrityError as exc:
            assert "ck_derivative_visual_versions_snapshot_hash" in str(exc)
        else:
            pytest.fail("a short source_snapshot_hash must be rejected")
        finally:
            conn.rollback()
    engine.dispose()


def test_two_owners_are_isolated(migrated_postgres):
    """The composite source FK prevents cross-owner Original snapshot refs."""
    engine = create_engine(migrated_postgres, poolclass=NullPool)
    a = _seed_owner(migrated_postgres, suffix=f"iso_a_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(migrated_postgres, suffix=f"iso_b_{uuid.uuid4().hex[:8]}")

    # Owner A's row referencing owner B's Original snapshot -> composite FK
    # (owner_id, novel_id, source_version_id) does not exist in owner A's scope.
    with engine.connect() as conn:
        try:
            conn.execute(
                text(
                    f"INSERT INTO {VERSIONS} (owner_id, novel_id, project_id, fork_id,"
                    " visual_namespace, version_key, revision_number, source_version_id,"
                    " source_snapshot_id, source_snapshot_hash, source_manifest_hash,"
                    " cutoff_chapter, divergence, provenance, review_state, schema_version,"
                    " schema_hash, policy_hash, manifest_hash, canonical_payload,"
                    " canonical_payload_hash, idempotency_key, projection_hash)"
                    " VALUES (:owner_id, :novel_id, :project_id, :fork_id,"
                    " 'fanfiction_visual', 'dv-foreign', 1, :source_version_id, 'snap-1',"
                    " :h, :h, 8, '{}'::jsonb, '{}'::jsonb, 'candidate', 'derivative-visual.v1',"
                    " :h, :h, :h, '{}'::jsonb, :h, :idem, :h)"
                ),
                {
                    "owner_id": a["owner_id"],
                    "novel_id": a["novel_id"],
                    "project_id": a["project_id"],
                    "fork_id": a["fork_id"],
                    "source_version_id": b["source_version_id"],
                    "h": HEX64,
                    "idem": _idem64(),
                },
            )
        except IntegrityError as exc:
            assert "fk_derivative_visual_versions_source_scope" in str(exc)
        else:
            pytest.fail("referencing another owner's Original snapshot must fail")
        finally:
            conn.rollback()
    engine.dispose()
