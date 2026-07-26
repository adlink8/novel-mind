"""
Phase 24-02: indexing_reconcile 单元测试（fake chroma client 注入）

覆盖:
- missing（DB 有向量无）/ orphan（向量有 DB 无）检出
- manifest 绑定：journal checksum 与 DB 复算比对
- repair：补 embed missing、删 orphan、reconcile_repair journal 审计、
  partial → ready 恢复
- repair 失败 fail-closed（journal 记 failed，novel 保持 partial）
- collection 不存在视为空、分页拉取
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# mock 掉 chromadb / litellm，避免未安装时 import 失败
sys.modules.setdefault("chromadb", MagicMock())
sys.modules.setdefault("litellm", MagicMock())

from app.models.chunk_index_journal import ChunkIndexJournal  # noqa: E402
from app.services.indexing_reconcile import (  # noqa: E402
    IndexReconcileError,
    IndexReconcileService,
)
from app.services.indexing_service import (  # noqa: E402
    compute_chunk_manifest_checksum,
)


# ── Fakes ──


class FakeCollection:
    def __init__(self, ids):
        self.ids = list(ids)
        self.deleted: list[str] = []

    def get(self, limit=None, offset=0, include=None):
        end = offset + (limit or len(self.ids))
        return {"ids": self.ids[offset:end]}

    def delete(self, ids):
        removal = set(ids)
        self.deleted.extend(ids)
        self.ids = [i for i in self.ids if i not in removal]


class FakeVectorStore:
    """注入用 fake：只实现 reconcile 依赖的接口。"""

    def __init__(self, ids=(), missing_collection=False):
        self.collection = FakeCollection(ids)
        self.missing_collection = missing_collection
        self.added_chunks: list[dict] = []

    def get_named_collection(self, name, create=False):
        if self.missing_collection:
            raise RuntimeError(f"Collection {name} does not exist")
        return self.collection

    async def add_chunks(self, novel_id, chunks):
        self.added_chunks.extend(chunks)
        self.collection.ids.extend(f"chunk_{c['id']}" for c in chunks)


class FakeEmbedder:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def embedding(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding provider down")
        return [[0.1, 0.2] for _ in texts]


def _row(chunk_id, content, status="embedded"):
    return SimpleNamespace(
        id=chunk_id,
        content=content,
        chapter_id=1,
        chunk_index=chunk_id - 1,
        chunk_type="paragraph",
        word_count=len(content),
        embedding_status=status,
    )


def _chunks_result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


def _journal_result(journal):
    result = MagicMock()
    result.scalars.return_value.first.return_value = journal
    return result


def _count_result(value):
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _sequenced_execute(*results):
    queue = list(results)

    async def _exec(*args, **kwargs):
        if queue:
            return queue.pop(0)
        return MagicMock()

    return _exec


def _mock_db(novel, *execute_results):
    db = AsyncMock()
    db.get = AsyncMock(return_value=novel)
    db.execute = AsyncMock(side_effect=_sequenced_execute(*execute_results))
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _novel(status="ready"):
    novel = MagicMock()
    novel.id = 1
    novel.status = status
    return novel


def _added_journals(db):
    return [
        c.args[0]
        for c in db.add.call_args_list
        if isinstance(c.args[0], ChunkIndexJournal)
    ]


# ── 检出 ──


class TestDetect:
    @pytest.mark.asyncio
    async def test_missing_and_orphan_detected(self):
        rows = [
            _row(1, "内容一"),
            _row(2, "内容二"),
            _row(3, "内容三", status="failed"),
        ]
        store = FakeVectorStore(ids=["chunk_1", "chunk_9"])
        db = _mock_db(_novel(), _chunks_result(rows), _journal_result(None))
        service = IndexReconcileService(store=store)

        report = await service.reconcile_novel(db, 1)

        assert report["db_chunks"] == 3
        assert report["chroma_vectors"] == 2
        assert report["missing"]["count"] == 2
        assert report["missing"]["chunk_ids"] == [2, 3]
        assert report["orphans"]["count"] == 1
        assert report["orphans"]["vector_ids"] == ["chunk_9"]
        assert report["consistent"] is False
        assert report["manifest"]["match"] is None  # 无 completed journal
        assert report["embedding_status_counts"] == {"embedded": 2, "failed": 1}
        assert report["repair"] is None

    @pytest.mark.asyncio
    async def test_consistent_when_sets_match(self):
        rows = [_row(1, "内容一"), _row(2, "内容二")]
        store = FakeVectorStore(ids=["chunk_1", "chunk_2"])
        db = _mock_db(_novel(), _chunks_result(rows), _journal_result(None))
        service = IndexReconcileService(store=store)

        report = await service.reconcile_novel(db, 1)

        assert report["consistent"] is True
        assert report["missing"]["count"] == 0
        assert report["orphans"]["count"] == 0

    @pytest.mark.asyncio
    async def test_missing_novel_raises(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        service = IndexReconcileService(store=FakeVectorStore())

        with pytest.raises(IndexReconcileError, match="不存在"):
            await service.reconcile_novel(db, 999)

    @pytest.mark.asyncio
    async def test_absent_collection_treated_as_empty(self):
        rows = [_row(1, "内容一")]
        store = FakeVectorStore(missing_collection=True)
        db = _mock_db(_novel(), _chunks_result(rows), _journal_result(None))
        service = IndexReconcileService(store=store)

        report = await service.reconcile_novel(db, 1)

        assert report["chroma_vectors"] == 0
        assert report["missing"]["count"] == 1

    @pytest.mark.asyncio
    async def test_collection_ids_paginated(self):
        ids = [f"chunk_{i}" for i in range(1500)]
        store = FakeVectorStore(ids=ids)
        service = IndexReconcileService(store=store)

        fetched = await service._collection_ids(1)

        assert len(fetched) == 1500


# ── manifest 绑定 ──


class TestManifestBinding:
    @pytest.mark.asyncio
    async def test_manifest_matches_completed_journal(self):
        rows = [_row(1, "内容一"), _row(2, "内容二")]
        checksum = compute_chunk_manifest_checksum([(r.id, r.content) for r in rows])
        journal = ChunkIndexJournal(
            novel_id=1,
            attempt_id="attempt-x",
            kind="index",
            phase="completed",
            manifest_checksum=checksum,
        )
        store = FakeVectorStore(ids=["chunk_1", "chunk_2"])
        db = _mock_db(_novel(), _chunks_result(rows), _journal_result(journal))
        service = IndexReconcileService(store=store)

        report = await service.reconcile_novel(db, 1)

        assert report["manifest"]["match"] is True
        assert report["manifest"]["journal_attempt_id"] == "attempt-x"

    @pytest.mark.asyncio
    async def test_manifest_mismatch_detected(self):
        rows = [_row(1, "内容一（已改动）")]
        journal = ChunkIndexJournal(
            novel_id=1,
            attempt_id="attempt-x",
            kind="index",
            phase="completed",
            manifest_checksum=compute_chunk_manifest_checksum([(1, "内容一")]),
        )
        store = FakeVectorStore(ids=["chunk_1"])
        db = _mock_db(_novel(), _chunks_result(rows), _journal_result(journal))
        service = IndexReconcileService(store=store)

        report = await service.reconcile_novel(db, 1)

        assert report["manifest"]["match"] is False


# ── repair ──


class TestRepair:
    @pytest.mark.asyncio
    async def test_repair_embeds_missing_deletes_orphans_and_recovers_status(self):
        rows = [
            _row(1, "内容一"),
            _row(2, "内容二", status="failed"),
            _row(3, "内容三", status="pending"),
        ]
        store = FakeVectorStore(ids=["chunk_1", "chunk_9"])
        embedder = FakeEmbedder()
        db = _mock_db(
            _novel(status="partial"),
            _chunks_result(rows),
            _journal_result(None),
            MagicMock(),  # update embedding_status
            _count_result(0),  # 无剩余 failed 块
        )
        service = IndexReconcileService(store=store, embedder=embedder)

        report = await service.reconcile_novel(db, 1, repair=True)

        repair = report["repair"]
        assert repair["embedded_missing"] == 2
        assert repair["failed"] == 0
        assert repair["deleted_orphans"] == 1
        assert store.collection.deleted == ["chunk_9"]
        assert {c["id"] for c in store.added_chunks} == {2, 3}
        # 修复后向量集包含全部 DB chunk
        assert set(store.collection.ids) == {"chunk_1", "chunk_2", "chunk_3"}
        # partial → ready 恢复
        assert repair["novel_status"] == "ready"
        assert report["novel_status"] == "ready"
        # journal 审计
        journal = _added_journals(db)[0]
        assert journal.kind == "reconcile_repair"
        assert journal.phase == "completed"
        assert journal.embedded_chunks == 2
        assert journal.manifest_checksum == report["manifest"]["recomputed"]
        assert journal.finished_at is not None

    @pytest.mark.asyncio
    async def test_repair_embed_failure_marks_journal_failed(self):
        rows = [_row(1, "内容一", status="failed")]
        store = FakeVectorStore(ids=[])
        embedder = FakeEmbedder(fail=True)
        novel = _novel(status="partial")
        db = _mock_db(novel, _chunks_result(rows), _journal_result(None), MagicMock())
        service = IndexReconcileService(store=store, embedder=embedder)

        report = await service.reconcile_novel(db, 1, repair=True)

        repair = report["repair"]
        assert repair["embedded_missing"] == 0
        assert repair["failed"] == 1
        assert repair["errors"]
        assert novel.status == "partial"  # 未修复不得声明 ready
        journal = _added_journals(db)[0]
        assert journal.phase == "failed"
        assert "down" in journal.error_summary

    @pytest.mark.asyncio
    async def test_repair_not_run_when_consistent(self):
        rows = [_row(1, "内容一")]
        store = FakeVectorStore(ids=["chunk_1"])
        db = _mock_db(_novel(), _chunks_result(rows), _journal_result(None))
        service = IndexReconcileService(store=store, embedder=FakeEmbedder())

        report = await service.reconcile_novel(db, 1, repair=True)

        assert report["consistent"] is True
        assert report["repair"] is None
        assert _added_journals(db) == []
