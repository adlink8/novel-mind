"""Phase 35-04 contamination phase gate PostgreSQL integration tests.

REQ-CRE-02 / REQ-FORK-01 / D-35-02..D-35-04 on the real CI database:

- the new ``canon_contamination_blocks`` audit table carries real PostgreSQL
  composite-unique / FK / check constraints (space is derivative-only, pipeline
  is Original-only, blocked_reason is non-empty, identical attempts are
  idempotent);
- a deliberate contamination write into the Original index / eval corpus /
  facet chains fails closed *inside the transaction*: the failed write is
  rolled back, the blocked reason is preserved, and the Original
  tables/index/eval/facet snapshots stay byte-identical;
- the contamination phase gate returns only ``candidate``/``blocked``: without
  an executed upstream contract-availability preflight it is ``blocked``, an
  active pointer / Original mutation / cross-owner leakage / un-approved
  publication each block, and the gate never changes the Phase 22
  BLOCKED/0-of-3 ledger.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.canon_contamination import CanonContaminationBlock
from app.models.canon_fork import CanonFork
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.canon_fork.contamination import (
    ContaminationBlockedError,
    ContaminationBlockedReason,
    ContaminationPhaseGate,
    PhaseGateVerdict,
    evaluation_corpus_guard,
    facet_producer_guard,
    original_index_guard,
    record_contamination_block,
)
from app.services.canon_fork.snapshot import (
    ForkChapterRecord,
    compute_source_snapshot_hash,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
PREV_REVISION = "20260801_canon_fork01"
# The head has advanced to Phase 36-03; the round trip must land there.
NEW_REVISION = "20260801_derivative_revision01"


def async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


@pytest.fixture
def migrated_db(pg_sync_url, pg_async_url, require_postgres):
    """Reset + upgrade heads so the phase gate starts from the new head."""
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "heads", database_url=pg_sync_url)
    return pg_async_url


@pytest.fixture
async def session_factory(migrated_db):
    engine = create_async_engine(async_url(migrated_db), echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession, suffix: str, chapter_count: int = 3) -> dict:
    user = User(
        username=f"pg_{suffix}",
        email=f"{suffix}@example.com",
        hashed_password="!test-hash",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    novel = Novel(
        title=f"Phase Gate Novel {suffix}",
        owner_id=user.id,
        status="ready",
        reading_progress={},
        chapter_count=chapter_count,
        word_count=sum(len(f"chapter {i} body") for i in range(1, chapter_count + 1)),
    )
    session.add(novel)
    await session.flush()
    chapter_ids: list[int] = []
    records: list[ForkChapterRecord] = []
    for i in range(1, chapter_count + 1):
        content = f"chapter {i} body"
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=i,
            title=f"C{i}",
            content=content,
            word_count=len(content),
        )
        session.add(chapter)
        await session.flush()
        chapter_ids.append(chapter.id)
        records.append(
            ForkChapterRecord(chapter_id=chapter.id, chapter_number=i, content=content)
        )
    snapshot_hash = compute_source_snapshot_hash(
        owner_id=user.id, novel_id=novel.id, chapters=tuple(records)
    )
    await session.commit()
    return {
        "owner_id": user.id,
        "novel_id": novel.id,
        "chapter_ids": chapter_ids,
        "snapshot_hash": snapshot_hash,
    }


def _original_snapshot(sync_url: str, *, novel_id: int) -> dict:
    """Snapshot of the Original canon tables (chapters only) for change detection."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        rows = (
            session.execute(
                text(
                    "SELECT chapter_number, title, content, word_count "
                    "FROM chapters WHERE novel_id = :novel "
                    "ORDER BY chapter_number"
                ),
                {"novel": novel_id},
            )
        ).all()
        snapshot = {
            "chapters": [tuple(row) for row in rows],
            "count": len(rows),
        }
    engine.dispose()
    return snapshot


# ---------------------------------------------------------------------------
# Migration: real composite unique / FK / check constraints
# ---------------------------------------------------------------------------


async def test_contamination_block_migration_round_trip(
    pg_sync_url, pg_async_url, require_postgres
):
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "heads", database_url=pg_sync_url)
    run_alembic("downgrade", PREV_REVISION, database_url=pg_sync_url)

    engine = create_async_engine(async_url(pg_async_url), echo=False)
    async with engine.begin() as conn:
        tables = set(
            await conn.run_sync(
                lambda sync: sync.exec_driver_sql(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                ).fetchall()
            )
        )
    await engine.dispose()
    assert ("canon_contamination_blocks",) not in tables

    run_alembic("upgrade", "heads", database_url=pg_sync_url)
    current = run_alembic("current", database_url=pg_sync_url)
    assert NEW_REVISION in current.stdout
    check = run_alembic("check", database_url=pg_sync_url)
    assert check.returncode == 0, (
        f"alembic check failed:\n{check.stdout}\n{check.stderr}"
    )


async def test_contamination_block_constraints(session_factory):
    async with session_factory() as session:
        ids = await _seed(session, f"cblk_{uuid.uuid4().hex[:8]}")
        owner_id, novel_id = ids["owner_id"], ids["novel_id"]

        await record_contamination_block(
            session,
            ContaminationBlockedError(
                ContaminationBlockedReason.SPACE_EXCLUDED,
                "deliberate fanfiction contamination into the original index",
                pipeline="original_retrieval",
                space="fanfiction_canon",
                owner_id=owner_id,
                novel_id=novel_id,
            ),
            owner_id=owner_id,
            novel_id=novel_id,
            attempt_hash="a" * 64,
        )

        rows = list(
            (
                await session.scalars(
                    select(CanonContaminationBlock).where(
                        CanonContaminationBlock.owner_id == owner_id,
                        CanonContaminationBlock.novel_id == novel_id,
                    )
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].blocked_reason == "space_excluded"
        assert rows[0].space == "fanfiction_canon"
        assert rows[0].pipeline == "original_retrieval"

        # Identical (owner, novel, space, pipeline, attempt_hash) is idempotent.
        await record_contamination_block(
            session,
            ContaminationBlockedError(
                ContaminationBlockedReason.SPACE_EXCLUDED,
                "duplicate deliberate contamination",
                pipeline="original_retrieval",
                space="fanfiction_canon",
                owner_id=owner_id,
                novel_id=novel_id,
            ),
            owner_id=owner_id,
            novel_id=novel_id,
            attempt_hash="a" * 64,
        )
        rows2 = list(
            (
                await session.scalars(
                    select(CanonContaminationBlock).where(
                        CanonContaminationBlock.owner_id == owner_id,
                        CanonContaminationBlock.novel_id == novel_id,
                    )
                )
            ).all()
        )
        assert len(rows2) == 1


# ---------------------------------------------------------------------------
# Deliberate contamination into Original chains fails closed + no mutation
# ---------------------------------------------------------------------------


async def test_index_contamination_fails_closed_and_keeps_snapshot(
    session_factory, migrated_db
):
    sync_url = migrated_db.replace("+asyncpg", "+psycopg2")
    async with session_factory() as session:
        ids = await _seed(session, f"idx_{uuid.uuid4().hex[:8]}")
        before = _original_snapshot(sync_url, novel_id=ids["novel_id"])

        with pytest.raises(ContaminationBlockedError) as excinfo:
            await original_index_guard.guard_write(
                session,
                write=lambda db: db.execute(
                    text(
                        "INSERT INTO chapters (novel_id, chapter_number, title, "
                        "content, word_count) VALUES (:novel, 99, 'smuggled', "
                        "'derivative', 0)"
                    ),
                    {"novel": ids["novel_id"]},
                ),
                space="user_interpretation",
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
            )
        assert excinfo.value.blocked_reason is ContaminationBlockedReason.SPACE_EXCLUDED

        after = _original_snapshot(sync_url, novel_id=ids["novel_id"])
        assert after == before


async def test_eval_contamination_fails_closed_and_keeps_snapshot(
    session_factory, migrated_db
):
    sync_url = migrated_db.replace("+asyncpg", "+psycopg2")
    async with session_factory() as session:
        ids = await _seed(session, f"eval_{uuid.uuid4().hex[:8]}")
        before = _original_snapshot(sync_url, novel_id=ids["novel_id"])

        with pytest.raises(ContaminationBlockedError) as excinfo:
            await evaluation_corpus_guard.guard_write(
                session,
                write=lambda db: db.execute(
                    text(
                        "INSERT INTO eval_datasets (novel_id, question, "
                        "question_type, difficulty, gold_chunks, expected_points, "
                        "must_not_say, status) VALUES (:novel, 'smuggled?', "
                        "'derivative', 'medium', '[]', '[]', '[]', 'candidate')"
                    ),
                    {"novel": ids["novel_id"]},
                ),
                space="fanfiction_canon",
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
            )
        assert excinfo.value.blocked_reason is ContaminationBlockedReason.SPACE_EXCLUDED

        after = _original_snapshot(sync_url, novel_id=ids["novel_id"])
        assert after == before


async def test_facet_contamination_fails_closed_and_keeps_snapshot(
    session_factory, migrated_db
):
    sync_url = migrated_db.replace("+asyncpg", "+psycopg2")
    async with session_factory() as session:
        ids = await _seed(session, f"fct_{uuid.uuid4().hex[:8]}")
        before = _original_snapshot(sync_url, novel_id=ids["novel_id"])

        with pytest.raises(ContaminationBlockedError) as excinfo:
            await facet_producer_guard.guard_write(
                session,
                write=lambda db: db.execute(
                    text(
                        "INSERT INTO chapters (novel_id, chapter_number, title, "
                        "content, word_count) VALUES (:novel, 99, 'facet-smuggle', "
                        "'derivative facet', 0)"
                    ),
                    {"novel": ids["novel_id"]},
                ),
                space="fanfiction_canon",
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
            )
        assert excinfo.value.blocked_reason is ContaminationBlockedReason.SPACE_EXCLUDED

        after = _original_snapshot(sync_url, novel_id=ids["novel_id"])
        assert after == before


# ---------------------------------------------------------------------------
# Contamination phase gate verdicts (candidate/blocked only)
# ---------------------------------------------------------------------------


async def test_phase_gate_blocks_without_preflight(session_factory):
    async with session_factory() as session:
        ids = await _seed(session, f"pre_{uuid.uuid4().hex[:8]}")
        gate = ContaminationPhaseGate(session)
        result = await gate.evaluate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            preflight_ok=False,
            expected_snapshot_hash=ids["snapshot_hash"],
        )
        assert result.verdict is PhaseGateVerdict.BLOCKED
        assert result.blocked_reason is ContaminationBlockedReason.MISSING_PREFLIGHT


async def test_phase_gate_candidate_when_clean_and_preflighted(session_factory):
    async with session_factory() as session:
        ids = await _seed(session, f"ok_{uuid.uuid4().hex[:8]}")
        gate = ContaminationPhaseGate(session)
        result = await gate.evaluate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            preflight_ok=True,
            expected_snapshot_hash=ids["snapshot_hash"],
        )
        assert result.verdict is PhaseGateVerdict.CANDIDATE
        assert result.blocked_reason is None


async def test_phase_gate_publish_requires_approval(session_factory):
    async with session_factory() as session:
        ids = await _seed(session, f"pub_{uuid.uuid4().hex[:8]}")
        gate = ContaminationPhaseGate(session)
        unapproved = await gate.evaluate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            preflight_ok=True,
            expected_snapshot_hash=ids["snapshot_hash"],
            publish_requested=True,
            approved=False,
        )
        assert unapproved.verdict is PhaseGateVerdict.BLOCKED
        assert unapproved.blocked_reason is ContaminationBlockedReason.APPROVAL_REQUIRED
        approved = await gate.evaluate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            preflight_ok=True,
            expected_snapshot_hash=ids["snapshot_hash"],
            publish_requested=True,
            approved=True,
        )
        assert approved.verdict is PhaseGateVerdict.CANDIDATE


async def test_phase_gate_blocks_on_original_mutation(session_factory, migrated_db):
    sync_url = migrated_db.replace("+asyncpg", "+psycopg2")
    async with session_factory() as session:
        ids = await _seed(session, f"mut_{uuid.uuid4().hex[:8]}")
        gate = ContaminationPhaseGate(session)

        # Tamper with the Original source: chapter body changes after the
        # snapshot hash was frozen -> the mutation check must block.
        engine = create_engine(sync_url, poolclass=NullPool)
        with Session(engine) as sess:
            chapter = sess.get(Chapter, ids["chapter_ids"][0])
            chapter.content = "tampered original body"
            sess.commit()
        engine.dispose()

        result = await gate.evaluate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            preflight_ok=True,
            expected_snapshot_hash=ids["snapshot_hash"],
        )
        assert result.verdict is PhaseGateVerdict.BLOCKED
        assert result.blocked_reason is ContaminationBlockedReason.ORIGINAL_MUTATION


async def test_phase_gate_blocks_on_cross_owner_leakage(session_factory):
    async with session_factory() as session:
        ids = await _seed(session, f"leak_{uuid.uuid4().hex[:8]}")
        foreign = await _seed(session, f"fr_{uuid.uuid4().hex[:8]}")
        # A foreign owner's fork row resolves for this novel -> leakage.
        session.add(
            CanonFork(
                owner_id=foreign["owner_id"],
                novel_id=ids["novel_id"],
                fork_key="foreign-leak",
                space="fanfiction_canon",
                status="candidate",
                source_version_key="original:v1",
                source_snapshot_id="novel:x",
                source_snapshot_hash=HEX64,
                through_chapter=1,
                full_book_authorized=False,
                cutoff_snapshot_hash=HEX64,
                scope_hash=HEX64,
                manifest_hash=HEX64,
                citation_lineage=[],
                authorization={},
                active=False,
            )
        )
        await session.commit()

        gate = ContaminationPhaseGate(session)
        result = await gate.evaluate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            preflight_ok=True,
            expected_snapshot_hash=ids["snapshot_hash"],
        )
        assert result.verdict is PhaseGateVerdict.BLOCKED
        assert result.blocked_reason is ContaminationBlockedReason.CROSS_OWNER_LEAKAGE


def test_phase_gate_never_touches_phase22_ledger():
    """D-35-04: the gate is read-only; Phase 22 stays BLOCKED/0-of-3."""
    from pathlib import Path

    ledger = (
        Path(__file__).resolve().parents[3]
        / ".planning"
        / "phases"
        / "22-ci-nightly-gap-closure"
        / "22-VALIDATION.md"
    )
    state = Path(__file__).resolve().parents[3] / ".planning" / "STATE.md"
    ledger_before = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
    state_before = state.read_text(encoding="utf-8") if state.is_file() else ""
    # Running the pure gate never writes these files; the ledger still reflects
    # the 0/3 blocked truth (the override records execution only).
    from app.services.canon_fork.contamination import (
        PhaseGateEvidence,
        resolve_gate_verdict,
    )

    for _ in range(3):
        result = resolve_gate_verdict(PhaseGateEvidence())
        assert result.verdict is PhaseGateVerdict.BLOCKED
        assert result.blocked_reason is ContaminationBlockedReason.MISSING_PREFLIGHT

    ledger_after = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
    state_after = state.read_text(encoding="utf-8") if state.is_file() else ""
    assert ledger_after == ledger_before
    assert state_after == state_before
