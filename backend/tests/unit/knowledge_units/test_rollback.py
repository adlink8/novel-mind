"""Unit tests for app.services.knowledge_units.rollback edge branches.

Existing coverage (tests/test_knowledge_unit_rollback.py) exercises the main
rollback/restore drill; this file targets the remaining branches: checkpoint
probe shape checks, checkpoint mismatch, journal state rejections, the
previous-build rollback path, watermark create/update/delete paths, and
advance_watermark mismatch guards.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.unit

from app.models import Novel, User
from app.models.knowledge_unit import (
    NarrativeActivePointer,
    NarrativeIndexBuild,
    NarrativePromotionJournal,
    NarrativeSourceSnapshot,
    NarrativeSourceWatermark,
)
from app.services.knowledge_units.reconcile import ReconcileReport
from app.services.knowledge_units.rollback import (
    RollbackError,
    _require_checkpoint,
    _restore_watermark,
    advance_watermark,
    collection_checkpoint_probe,
    rollback_journal,
    restore_journal,
)


# ── minimal fixtures ──


async def _mk_snapshot(db: AsyncSession, *, domain: str = "fiction"):
    user = User(
        username=f"rb_{domain}",
        email=f"rb_{domain}@example.com",
        hashed_password="x",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    novel = Novel(owner_id=user.id, title="rb novel", status="ready")
    db.add(novel)
    await db.flush()
    snapshot = NarrativeSourceSnapshot(
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile=domain,
        ontology_profile=f"{domain}.v1",
        status="frozen",
        source_watermark="wm",
        manifest_checksum="s" * 64,
        item_count=1,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def _mk_build(
    db: AsyncSession,
    *,
    snapshot: NarrativeSourceSnapshot,
    build_key: str,
    status: str,
    checksum: str,
    collection: str,
    domain: str = "fiction",
) -> NarrativeIndexBuild:
    build = NarrativeIndexBuild(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        source_snapshot_id=snapshot.id,
        domain_profile=domain,
        build_key=build_key,
        status=status,
        manifest_checksum=checksum,
        config_checksum="c" * 64,
        unit_count=0,
        collection_name=collection,
    )
    db.add(build)
    await db.flush()
    return build


async def _mk_journal(
    db: AsyncSession,
    *,
    candidate,
    previous=None,
    status="committed",
    before=None,
    after=None,
    candidate_checksum=None,
    previous_checksum=None,
) -> NarrativePromotionJournal:
    journal = NarrativePromotionJournal(
        owner_id=candidate.owner_id,
        novel_id=candidate.novel_id,
        domain_profile=candidate.domain_profile,
        transaction_key=f"rb-{candidate.build_key}-{status}-{previous.build_key if previous else 'none'}",
        candidate_build_id=candidate.id,
        previous_build_id=previous.id if previous else None,
        status=status,
        candidate_checksum=candidate_checksum or candidate.manifest_checksum,
        previous_checksum=previous_checksum,
        details={
            "before": before
            or (
                {
                    "build_id": previous.id if previous else None,
                    "collection": previous.collection_name if previous else None,
                    "manifest": previous.manifest_checksum if previous else None,
                    "watermark": None,
                }
            ),
            "after": after
            or {
                "build_id": candidate.id,
                "collection": candidate.collection_name,
                "manifest": candidate.manifest_checksum,
                "watermark": None,
            },
        },
    )
    db.add(journal)
    await db.flush()
    return journal


async def _mk_pointer(db, *, build, snapshot, pointer_version=1, checksum=None):
    pointer = NarrativeActivePointer(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile=snapshot.domain_profile,
        build_id=build.id,
        pointer_version=pointer_version,
        active_manifest_checksum=checksum or build.manifest_checksum,
        activated_at=datetime.now(UTC),
    )
    db.add(pointer)
    await db.flush()
    return pointer


async def _clean_probe(checkpoint):
    return True


# ── collection_checkpoint_probe ──


class _Collection:
    def __init__(self, ids=None, metadatas=None):
        self.ids = ids if ids is not None else []
        self.metadatas = metadatas if metadatas is not None else []

    def get(self, include=None):
        return {"ids": list(self.ids), "metadatas": list(self.metadatas)}


class _Store:
    def __init__(self, collection=None, raises=False):
        self._collection = collection
        self._raises = raises

    def get_named_collection(self, name):
        if self._raises:
            raise KeyError(name)
        return self._collection


def _checkpoint(build):
    return {
        "collection": build.collection_name,
        "manifest": build.manifest_checksum,
        "build_id": build.id,
    }


@pytest.mark.asyncio
async def test_probe_rejects_missing_fields():
    probe = collection_checkpoint_probe(_Store(_Collection()))
    assert await probe({}) is False
    assert await probe({"collection": "c"}) is False
    assert await probe({"collection": "c", "manifest": "m"}) is False
    assert await probe({"collection": "c", "manifest": "m", "build_id": None}) is False


@pytest.mark.asyncio
async def test_probe_rejects_store_errors():
    probe = collection_checkpoint_probe(_Store(raises=True))
    assert await probe({"collection": "c", "manifest": "m", "build_id": 1}) is False


@pytest.mark.asyncio
async def test_probe_rejects_empty_or_mismatched_payload(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="probe-build",
        status="active",
        checksum="c" * 64,
        collection="probe_col",
    )
    cp = _checkpoint(build)

    # Empty ids → False.
    probe = collection_checkpoint_probe(_Store(_Collection([], [])))
    assert await probe(cp) is False

    # Mismatched lengths → False.
    probe = collection_checkpoint_probe(
        _Store(_Collection(["u1"], [{"build_id": build.id}] + [None]))
    )
    assert await probe(cp) is False

    # Falsy metadata → False.
    probe = collection_checkpoint_probe(_Store(_Collection(["u1"], [None])))
    assert await probe(cp) is False

    # Wrong build_id in metadata → False.
    probe = collection_checkpoint_probe(
        _Store(
            _Collection(
                ["u1"],
                [{"build_id": 999, "manifest_checksum": build.manifest_checksum}],
            )
        )
    )
    assert await probe(cp) is False

    # Wrong manifest → False.
    probe = collection_checkpoint_probe(
        _Store(
            _Collection(["u1"], [{"build_id": build.id, "manifest_checksum": "bad"}])
        )
    )
    assert await probe(cp) is False

    # All match → True.
    probe = collection_checkpoint_probe(
        _Store(
            _Collection(
                ["u1"],
                [{"build_id": build.id, "manifest_checksum": build.manifest_checksum}],
            )
        )
    )
    assert await probe(cp) is True


# ── _require_checkpoint ──


@pytest.mark.asyncio
async def test_require_checkpoint_rejects_mismatch(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="req-checkpoint",
        status="active",
        checksum="c" * 64,
        collection="req_col",
    )
    checkpoint = _checkpoint(build)
    checkpoint["build_id"] = 555
    with pytest.raises(RollbackError, match="does not match PostgreSQL"):
        await _require_checkpoint(
            checkpoint, build=build, collection_probe=_clean_probe
        )


@pytest.mark.asyncio
async def test_require_checkpoint_rejects_unrecoverable(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="req-unrecoverable",
        status="active",
        checksum="c" * 64,
        collection="req_col",
    )

    async def failing_probe(checkpoint):
        return False

    with pytest.raises(RollbackError, match="not recoverable"):
        await _require_checkpoint(
            _checkpoint(build), build=build, collection_probe=failing_probe
        )


# ── rollback_journal rejections ──


@pytest.mark.asyncio
async def test_rollback_rejects_journal_not_committed(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-not-committed",
        status="candidate",
        checksum="c" * 64,
        collection="rb_col",
    )
    journal = await _mk_journal(db_session, candidate=candidate, status="prepared")
    with pytest.raises(RollbackError, match="not committed"):
        await rollback_journal(
            db_session, journal_id=journal.id, collection_probe=_clean_probe
        )


@pytest.mark.asyncio
async def test_rollback_rejects_missing_journal(db_session):
    with pytest.raises(RollbackError, match="not committed"):
        await rollback_journal(
            db_session, journal_id=999999, collection_probe=_clean_probe
        )


@pytest.mark.asyncio
async def test_rollback_rejects_pointer_mismatch(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-pointer-mismatch",
        status="candidate",
        checksum="c" * 64,
        collection="rb_col",
    )
    journal = await _mk_journal(db_session, candidate=candidate)
    other = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-pointer-other",
        status="active",
        checksum="d" * 64,
        collection="other_col",
    )
    await _mk_pointer(db_session, build=other, snapshot=snapshot)
    with pytest.raises(RollbackError, match="no longer matches journal"):
        await rollback_journal(
            db_session, journal_id=journal.id, collection_probe=_clean_probe
        )


@pytest.mark.asyncio
async def test_rollback_rejects_missing_pointer(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-no-pointer",
        status="candidate",
        checksum="c" * 64,
        collection="rb_col",
    )
    journal = await _mk_journal(db_session, candidate=candidate)
    with pytest.raises(RollbackError, match="no longer matches journal"):
        await rollback_journal(
            db_session, journal_id=journal.id, collection_probe=_clean_probe
        )


@pytest.mark.asyncio
async def test_rollback_rejects_missing_candidate(db_session, monkeypatch):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-missing-candidate",
        status="candidate",
        checksum="c" * 64,
        collection="rb_col",
    )
    journal = await _mk_journal(db_session, candidate=candidate)
    await _mk_pointer(db_session, build=candidate, snapshot=snapshot)

    # 真实候选行无法删除（pointer/journal FK），改为让 db.get 返回 None 模拟缺失。
    original_get = db_session.get

    async def fake_get(model, pk):
        if model is NarrativeIndexBuild and pk == candidate.id:
            return None
        return await original_get(model, pk)

    monkeypatch.setattr(db_session, "get", fake_get)
    with pytest.raises(RollbackError, match="candidate build is missing"):
        await rollback_journal(
            db_session, journal_id=journal.id, collection_probe=_clean_probe
        )


@pytest.mark.asyncio
async def test_rollback_rejects_missing_previous_build(db_session, monkeypatch):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-missing-prev",
        status="candidate",
        checksum="c" * 64,
        collection="rb_col",
    )
    previous = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-prev-ghost",
        status="active",
        checksum="p" * 64,
        collection="prev_col",
    )
    journal = NarrativePromotionJournal(
        owner_id=candidate.owner_id,
        novel_id=candidate.novel_id,
        domain_profile=candidate.domain_profile,
        transaction_key="rb-missing-prev-journal",
        candidate_build_id=candidate.id,
        previous_build_id=previous.id,
        status="committed",
        candidate_checksum=candidate.manifest_checksum,
        previous_checksum="p" * 64,
        details={
            "before": {"watermark": None},
            "after": {"watermark": None},
        },
    )
    db_session.add(journal)
    await db_session.flush()
    await _mk_pointer(db_session, build=candidate, snapshot=snapshot)

    # 让 previous build 在 db.get 时“消失”，触发 previous build missing。
    original_get = db_session.get

    async def fake_get(model, pk):
        if model is NarrativeIndexBuild and pk == previous.id:
            return None
        return await original_get(model, pk)

    monkeypatch.setattr(db_session, "get", fake_get)
    with pytest.raises(RollbackError, match="previous build is missing"):
        await rollback_journal(
            db_session, journal_id=journal.id, collection_probe=_clean_probe
        )


# ── rollback/restore with a previous build ──


@pytest.mark.asyncio
async def test_rollback_restores_previous_pointer_and_restore_is_reversible(db_session):
    snapshot = await _mk_snapshot(db_session)
    previous = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-prev",
        status="active",
        checksum="p" * 64,
        collection="prev_col",
    )
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="rb-cand",
        status="active",
        checksum="c" * 64,
        collection="cand_col",
    )
    pointer = await _mk_pointer(db_session, build=candidate, snapshot=snapshot)
    journal = await _mk_journal(
        db_session,
        candidate=candidate,
        previous=previous,
        candidate_checksum="c" * 64,
        previous_checksum="p" * 64,
    )

    result = await rollback_journal(
        db_session, journal_id=journal.id, collection_probe=_clean_probe
    )
    assert result is pointer
    assert result.build_id == previous.id
    assert result.pointer_version == 2
    assert result.active_manifest_checksum == "p" * 64
    assert candidate.status == "rolled_back"
    assert previous.status == "active"
    assert journal.status == "rolled_back"

    # Restore back to the candidate.
    restored = await restore_journal(
        db_session, journal_id=journal.id, collection_probe=_clean_probe
    )
    assert restored is pointer
    assert restored.build_id == candidate.id
    assert restored.pointer_version == 3
    assert candidate.status == "active"
    assert journal.status == "committed"
    assert (
        await db_session.get(NarrativeIndexBuild, previous.id)
    ).status == "deprecated"


# ── restore_journal rejections ──


@pytest.mark.asyncio
async def test_restore_rejects_journal_not_rolled_back(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="restore-not-rolled",
        status="candidate",
        checksum="c" * 64,
        collection="rb_col",
    )
    journal = await _mk_journal(db_session, candidate=candidate, status="committed")
    with pytest.raises(RollbackError, match="not rolled back"):
        await restore_journal(
            db_session, journal_id=journal.id, collection_probe=_clean_probe
        )


@pytest.mark.asyncio
async def test_restore_rejects_missing_candidate(db_session, monkeypatch):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="restore-missing-cand",
        status="candidate",
        checksum="c" * 64,
        collection="rb_col",
    )
    journal = await _mk_journal(db_session, candidate=candidate, status="rolled_back")
    original_get = db_session.get

    async def fake_get(model, pk):
        if model is NarrativeIndexBuild and pk == candidate.id:
            return None
        return await original_get(model, pk)

    monkeypatch.setattr(db_session, "get", fake_get)
    with pytest.raises(RollbackError, match="candidate build is missing"):
        await restore_journal(
            db_session, journal_id=journal.id, collection_probe=_clean_probe
        )


@pytest.mark.asyncio
async def test_restore_rejects_pointer_changed_during_rollback(db_session):
    snapshot = await _mk_snapshot(db_session)
    previous = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="restore-prev",
        status="active",
        checksum="p" * 64,
        collection="prev_col",
    )
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="restore-cand",
        status="active",
        checksum="c" * 64,
        collection="cand_col",
    )
    journal = await _mk_journal(
        db_session,
        candidate=candidate,
        previous=previous,
        candidate_checksum="c" * 64,
        previous_checksum="p" * 64,
        status="rolled_back",
    )
    # Pointer should point at previous after a rollback, but simulate drift.
    await _mk_pointer(db_session, build=candidate, snapshot=snapshot)
    with pytest.raises(RollbackError, match="rollback target changed"):
        await restore_journal(
            db_session, journal_id=journal.id, collection_probe=_clean_probe
        )


# ── _restore_watermark ──


async def _watermark_journal(
    db, *, candidate, previous=None, side="before", checkpoint
):
    journal = await _mk_journal(
        db,
        candidate=candidate,
        previous=previous,
        candidate_checksum=candidate.manifest_checksum,
        previous_checksum=previous.manifest_checksum if previous else None,
        status="rolled_back",
        before={"watermark": checkpoint},
        after={"watermark": None},
    )
    journal.details["before"]["watermark"] = checkpoint
    await db.flush()
    return journal


@pytest.mark.asyncio
async def test_restore_watermark_deletes_row_when_checkpoint_none(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="wm-delete",
        status="candidate",
        checksum="c" * 64,
        collection="wm_col",
    )
    watermark = NarrativeSourceWatermark(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile=snapshot.domain_profile,
        snapshot_id=snapshot.id,
        build_id=candidate.id,
        source_watermark="wm",
        manifest_checksum="c" * 64,
    )
    db_session.add(watermark)
    await db_session.flush()
    journal = await _watermark_journal(db_session, candidate=candidate, checkpoint=None)
    await _restore_watermark(db_session, journal, "before")
    assert (
        await db_session.scalar(
            select(NarrativeSourceWatermark).where(
                NarrativeSourceWatermark.owner_id == snapshot.owner_id
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_restore_watermark_noop_when_no_checkpoint_no_row(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="wm-noop",
        status="candidate",
        checksum="c" * 64,
        collection="wm_col",
    )
    journal = await _watermark_journal(db_session, candidate=candidate, checkpoint=None)
    await _restore_watermark(db_session, journal, "before")
    assert await db_session.scalar(select(NarrativeSourceWatermark)) is None


@pytest.mark.asyncio
async def test_restore_watermark_rejects_missing_snapshot(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="wm-missing-snap",
        status="candidate",
        checksum="c" * 64,
        collection="wm_col",
    )
    checkpoint = {
        "snapshot_id": 999999,
        "build_id": candidate.id,
        "manifest_checksum": "m" * 64,
    }
    journal = await _watermark_journal(
        db_session, candidate=candidate, checkpoint=checkpoint
    )
    with pytest.raises(RollbackError, match="watermark snapshot checkpoint is missing"):
        await _restore_watermark(db_session, journal, "before")


@pytest.mark.asyncio
async def test_restore_watermark_creates_row(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="wm-create",
        status="candidate",
        checksum="c" * 64,
        collection="wm_col",
    )
    checkpoint = {
        "snapshot_id": snapshot.id,
        "build_id": candidate.id,
        "manifest_checksum": "m" * 64,
    }
    journal = await _watermark_journal(
        db_session, candidate=candidate, checkpoint=checkpoint
    )
    await _restore_watermark(db_session, journal, "before")
    row = await db_session.scalar(select(NarrativeSourceWatermark))
    assert row is not None
    assert row.snapshot_id == snapshot.id
    assert row.build_id == candidate.id
    assert row.manifest_checksum == "m" * 64
    assert row.source_watermark == snapshot.source_watermark


@pytest.mark.asyncio
async def test_restore_watermark_updates_existing_row(db_session):
    snapshot = await _mk_snapshot(db_session)
    candidate = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="wm-update",
        status="candidate",
        checksum="c" * 64,
        collection="wm_col",
    )
    other = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="wm-update-other",
        status="active",
        checksum="o" * 64,
        collection="other_col",
    )
    existing = NarrativeSourceWatermark(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile=snapshot.domain_profile,
        snapshot_id=snapshot.id,
        build_id=other.id,
        source_watermark="old",
        manifest_checksum="o" * 64,
    )
    db_session.add(existing)
    await db_session.flush()
    checkpoint = {
        "snapshot_id": snapshot.id,
        "build_id": candidate.id,
        "manifest_checksum": "m" * 64,
    }
    journal = await _watermark_journal(
        db_session, candidate=candidate, checkpoint=checkpoint
    )
    await _restore_watermark(db_session, journal, "before")
    await db_session.flush()  # _restore_watermark 只改属性，需 flush 后 refresh 才可见
    await db_session.refresh(existing)
    assert existing.build_id == candidate.id
    assert existing.manifest_checksum == "m" * 64
    assert existing.source_watermark == snapshot.source_watermark


# ── advance_watermark guards ──


def _clean_reconcile(build_id):
    return ReconcileReport(
        build_id=build_id,
        expected=(),
        actual=(),
        missing=(),
        orphan=(),
        duplicate=(),
        wrong_build=(),
        wrong_owner=(),
        deleted=(),
        deprecated=(),
    )


@pytest.mark.asyncio
async def test_advance_watermark_rejects_build_snapshot_mismatch(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="aw-mismatch",
        status="active",
        checksum="c" * 64,
        collection="aw_col",
    )
    await _mk_pointer(db_session, build=build, snapshot=snapshot)
    with pytest.raises(RollbackError, match="active build/snapshot mismatch"):
        await advance_watermark(
            db_session,
            build_id=build.id,
            snapshot_id=999999,
            reconcile=_clean_reconcile(build.id),
        )


@pytest.mark.asyncio
async def test_advance_watermark_rejects_non_active_build(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="aw-not-active",
        status="candidate",
        checksum="c" * 64,
        collection="aw_col",
    )
    await _mk_pointer(db_session, build=build, snapshot=snapshot)
    with pytest.raises(RollbackError, match="active build/snapshot mismatch"):
        await advance_watermark(
            db_session,
            build_id=build.id,
            snapshot_id=snapshot.id,
            reconcile=_clean_reconcile(build.id),
        )


@pytest.mark.asyncio
async def test_advance_watermark_rejects_pointer_mismatch(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="aw-pointer-mismatch",
        status="active",
        checksum="c" * 64,
        collection="aw_col",
    )
    with pytest.raises(RollbackError, match="active pointer is not reconciled"):
        await advance_watermark(
            db_session,
            build_id=build.id,
            snapshot_id=snapshot.id,
            reconcile=_clean_reconcile(build.id),
        )


@pytest.mark.asyncio
async def test_advance_watermark_rejects_pointer_checksum_mismatch(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="aw-checksum-mismatch",
        status="active",
        checksum="c" * 64,
        collection="aw_col",
    )
    await _mk_pointer(db_session, build=build, snapshot=snapshot, checksum="wrong" * 16)
    with pytest.raises(RollbackError, match="active pointer is not reconciled"):
        await advance_watermark(
            db_session,
            build_id=build.id,
            snapshot_id=snapshot.id,
            reconcile=_clean_reconcile(build.id),
        )


@pytest.mark.asyncio
async def test_advance_watermark_updates_existing_row(db_session):
    snapshot = await _mk_snapshot(db_session)
    build = await _mk_build(
        db_session,
        snapshot=snapshot,
        build_key="aw-update",
        status="active",
        checksum="c" * 64,
        collection="aw_col",
    )
    await _mk_pointer(db_session, build=build, snapshot=snapshot)
    existing = NarrativeSourceWatermark(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile=snapshot.domain_profile,
        snapshot_id=snapshot.id,
        build_id=build.id,
        source_watermark="old",
        manifest_checksum="old" * 16,
    )
    db_session.add(existing)
    await db_session.flush()

    result = await advance_watermark(
        db_session,
        build_id=build.id,
        snapshot_id=snapshot.id,
        reconcile=_clean_reconcile(build.id),
    )
    assert result.id == existing.id
    assert result.manifest_checksum == build.manifest_checksum
    assert result.source_watermark == snapshot.source_watermark
