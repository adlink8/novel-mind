"""
Phase 24-01: chunk_index_journal / 幂等 / fail-closed 单元测试

覆盖:
- journal 生命周期（completed + manifest_checksum）
- Chroma 删除失败 fail-closed（不再删 DB 行、journal 记 failed）
- failed_count > 0 时 novel.status = "partial"（fail-closed）
- 幂等：进行中拒绝并发；force=True 接管；同内容 completed 跳过
- checksum 帮助函数确定性
- hybrid search 对 partial 小说的 additive index_status 标注
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# mock 掉 chromadb / litellm，避免未安装时 import 失败
sys.modules.setdefault("chromadb", MagicMock())
sys.modules.setdefault("litellm", MagicMock())

from sqlalchemy.sql.dml import Delete  # noqa: E402

from app.models.chunk_index_journal import ChunkIndexJournal  # noqa: E402
from app.models.text_chunk import TextChunk  # noqa: E402
from app.services.indexing_service import (  # noqa: E402
    IndexingError,
    IndexingService,
    compute_chunk_manifest_checksum,
    compute_source_signature,
)


# ── Fixtures（与 test_indexing.py 同构） ──


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def mock_novel():
    novel = MagicMock()
    novel.id = 1
    novel.title = "测试小说"
    novel.status = "importing"
    return novel


@pytest.fixture
def mock_chapters():
    chapters = []
    for i in range(1, 4):
        ch = MagicMock()
        ch.id = i
        ch.chapter_number = i
        ch.content = f"第{i}章内容。" * 100
        chapters.append(ch)
    return chapters


@pytest.fixture
def mock_chunking_service():
    service = AsyncMock()
    service.chunk_novel = AsyncMock(
        return_value=[
            {
                "content": f"第{i}个文本块",
                "chunk_type": "paragraph",
                "chunk_index": i - 1,
                "word_count": 20,
                "metadata_json": {"chapter_id": (i - 1) // 2 + 1},
            }
            for i in range(1, 4)
        ]
    )
    return service


@pytest.fixture
def mock_vector_store():
    store = AsyncMock()
    store.add_chunks = AsyncMock()
    store.delete_novel_chunks = AsyncMock()
    return store


@pytest.fixture
def indexing_service(mock_chunking_service, mock_vector_store):
    service = IndexingService()
    service.chunking_service = mock_chunking_service
    service.vector_store = mock_vector_store
    return service


def _chapters_result(chapters):
    result = MagicMock()
    result.scalars.return_value.all.return_value = chapters
    return result


def _journal_result(journal):
    result = MagicMock()
    result.scalars.return_value.first.return_value = journal
    return result


def _sequenced_execute(*results):
    """前 N 次 execute 依序返回给定结果，之后返回空 MagicMock。"""
    queue = list(results)

    async def _exec(*args, **kwargs):
        if queue:
            return queue.pop(0)
        return MagicMock()

    return _exec


def _payload(chapters):
    return [
        {"id": ch.id, "chapter_number": ch.chapter_number, "content": ch.content}
        for ch in chapters
    ]


def _added_journals(mock_db):
    return [
        c.args[0]
        for c in mock_db.add.call_args_list
        if isinstance(c.args[0], ChunkIndexJournal)
    ]


# ── journal 生命周期 ──


class TestJournalLifecycle:
    @pytest.mark.asyncio
    async def test_success_writes_completed_journal_with_manifest(
        self, indexing_service, mock_db, mock_novel, mock_chapters
    ):
        mock_db.get.return_value = mock_novel
        mock_db.execute.return_value = _chapters_result(mock_chapters)

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1, 0.2]] * 3)
            result = await indexing_service.index_novel(mock_db, novel_id=1)

        journals = _added_journals(mock_db)
        assert len(journals) == 1
        journal = journals[0]
        assert journal.phase == "completed"
        assert journal.kind == "index"
        assert journal.novel_id == 1
        assert journal.collection_name == "novel_1"
        assert journal.total_chunks == 3
        assert journal.embedded_chunks == 3
        assert journal.failed_chunks == 0
        assert journal.source_signature == compute_source_signature(
            _payload(mock_chapters)
        )
        assert isinstance(journal.manifest_checksum, str)
        assert len(journal.manifest_checksum) == 64
        assert journal.finished_at is not None
        assert result["attempt_id"] == journal.attempt_id
        assert result["status"] == "ready"

    @pytest.mark.asyncio
    async def test_partial_failure_marks_novel_partial_and_journal_failed(
        self, indexing_service, mock_db, mock_novel, mock_chapters
    ):
        mock_db.get.return_value = mock_novel
        mock_db.execute.return_value = _chapters_result(mock_chapters)

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(side_effect=RuntimeError("embedding down"))
            result = await indexing_service.index_novel(mock_db, novel_id=1)

        assert result["status"] == "partial"
        assert mock_novel.status == "partial"
        journal = _added_journals(mock_db)[0]
        assert journal.phase == "failed"
        assert journal.failed_chunks == 3
        assert journal.embedded_chunks == 0
        assert journal.manifest_checksum is None
        assert "partial" in journal.error_summary
        assert journal.finished_at is not None


# ── fail-closed 删除窗口 ──


class TestFailClosedDeletion:
    @pytest.mark.asyncio
    async def test_chroma_delete_failure_aborts_before_db_delete(
        self, indexing_service, mock_db, mock_novel, mock_chapters, mock_vector_store
    ):
        """Chroma 删除失败 → fail-closed 抛错，DB 行未删，journal 记 failed。"""
        mock_db.get.return_value = mock_novel
        mock_db.execute.return_value = _chapters_result(mock_chapters)
        mock_vector_store.delete_novel_chunks.side_effect = RuntimeError("chroma down")

        with pytest.raises(IndexingError, match="Chroma"):
            await indexing_service.index_novel(mock_db, novel_id=1)

        # DB text_chunks 的 DELETE 从未被执行（旧实现先删 DB，会留下残留窗口）
        delete_calls = [
            c
            for c in mock_db.execute.call_args_list
            if c.args and isinstance(c.args[0], Delete)
        ]
        assert delete_calls == []

        journal = _added_journals(mock_db)[0]
        assert journal.phase == "failed"
        assert "Chroma" in journal.error_summary
        assert mock_novel.status == "failed"


# ── 幂等键 ──


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_in_progress_attempt_rejects_concurrent_run(
        self, indexing_service, mock_db, mock_novel, mock_chapters
    ):
        in_progress = ChunkIndexJournal(
            novel_id=1,
            attempt_id="attempt-a",
            kind="index",
            phase="embedding",
            finished_at=None,
        )
        mock_db.get.return_value = mock_novel
        mock_db.execute.side_effect = _sequenced_execute(
            _chapters_result(mock_chapters), _journal_result(in_progress)
        )

        with pytest.raises(IndexingError, match="进行中"):
            await indexing_service.index_novel(mock_db, novel_id=1)

        assert _added_journals(mock_db) == []

    @pytest.mark.asyncio
    async def test_force_supersedes_in_progress_attempt(
        self, indexing_service, mock_db, mock_novel, mock_chapters
    ):
        in_progress = ChunkIndexJournal(
            novel_id=1,
            attempt_id="attempt-a",
            kind="index",
            phase="embedding",
            finished_at=None,
        )
        mock_db.get.return_value = mock_novel
        mock_db.execute.side_effect = _sequenced_execute(
            _chapters_result(mock_chapters), _journal_result(in_progress)
        )

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)
            result = await indexing_service.index_novel(mock_db, novel_id=1, force=True)

        assert in_progress.phase == "failed"
        assert "superseded" in in_progress.error_summary
        assert in_progress.finished_at is not None
        assert result["status"] == "ready"
        assert len(_added_journals(mock_db)) == 1

    @pytest.mark.asyncio
    async def test_completed_same_signature_skips_reindex(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
    ):
        signature = compute_source_signature(_payload(mock_chapters))
        completed = ChunkIndexJournal(
            novel_id=1,
            attempt_id="attempt-done",
            kind="index",
            phase="completed",
            total_chunks=3,
            embedded_chunks=3,
            failed_chunks=0,
            source_signature=signature,
        )
        completed.finished_at = MagicMock()  # 非 None 即可
        mock_db.get.return_value = mock_novel
        mock_db.execute.side_effect = _sequenced_execute(
            _chapters_result(mock_chapters), _journal_result(completed)
        )

        result = await indexing_service.index_novel(mock_db, novel_id=1)

        assert result["skipped"] is True
        assert result["status"] == "ready"
        assert result["attempt_id"] == "attempt-done"
        mock_chunking_service.chunk_novel.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_different_signature_reindexes(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
    ):
        completed = ChunkIndexJournal(
            novel_id=1,
            attempt_id="attempt-old",
            kind="index",
            phase="completed",
            total_chunks=3,
            embedded_chunks=3,
            failed_chunks=0,
            source_signature="deadbeef" * 8,
        )
        completed.finished_at = MagicMock()
        mock_db.get.return_value = mock_novel
        mock_db.execute.side_effect = _sequenced_execute(
            _chapters_result(mock_chapters), _journal_result(completed)
        )

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)
            result = await indexing_service.index_novel(mock_db, novel_id=1)

        assert result.get("skipped") is not True
        mock_chunking_service.chunk_novel.assert_called_once()


# ── checksum 帮助函数 ──


class TestChecksums:
    def test_manifest_checksum_is_order_independent(self):
        a = compute_chunk_manifest_checksum([(1, "内容一"), (2, "内容二")])
        b = compute_chunk_manifest_checksum([(2, "内容二"), (1, "内容一")])
        assert a == b
        assert len(a) == 64

    def test_manifest_checksum_changes_with_content(self):
        a = compute_chunk_manifest_checksum([(1, "内容一")])
        b = compute_chunk_manifest_checksum([(1, "内容二")])
        assert a != b

    def test_source_signature_stable_and_content_sensitive(self):
        payload = [{"id": 1, "chapter_number": 1, "content": "第一章"}]
        assert compute_source_signature(payload) == compute_source_signature(payload)
        changed = [{"id": 1, "chapter_number": 1, "content": "第一章改"}]
        assert compute_source_signature(payload) != compute_source_signature(changed)


# ── hybrid search additive index_status ──


class TestHybridIndexStatusMetadata:
    @pytest.mark.asyncio
    async def test_partial_novel_results_are_annotated(self):
        from app.services.hybrid_search import HybridSearchService

        service = HybridSearchService()
        db = AsyncMock()
        rows_result = MagicMock()
        rows_result.all.return_value = [
            SimpleNamespace(id=1, status="partial"),
            SimpleNamespace(id=2, status="ready"),
        ]
        db.execute = AsyncMock(return_value=rows_result)

        results = [
            {"novel_id": 1, "chunk_id": 10, "score": 0.5},
            {"novel_id": 2, "chunk_id": 20, "score": 0.4},
        ]
        out = await service._attach_index_status(db, results)

        assert out[0]["index_status"] == "partial"
        assert "index_status" not in out[1]

    @pytest.mark.asyncio
    async def test_annotation_failure_never_breaks_search(self):
        from app.services.hybrid_search import HybridSearchService

        service = HybridSearchService()
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db down"))

        results = [{"novel_id": 1, "chunk_id": 10, "score": 0.5}]
        out = await service._attach_index_status(db, results)

        assert out == results
        assert "index_status" not in out[0]


# ── TextChunk 记录与 journal 分离健全性 ──


class TestJournalDoesNotPolluteChunks:
    @pytest.mark.asyncio
    async def test_added_objects_split_between_journal_and_chunks(
        self, indexing_service, mock_db, mock_novel, mock_chapters
    ):
        mock_db.get.return_value = mock_novel
        mock_db.execute.return_value = _chapters_result(mock_chapters)

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)
            await indexing_service.index_novel(mock_db, novel_id=1)

        added = [c.args[0] for c in mock_db.add.call_args_list]
        assert len([o for o in added if isinstance(o, ChunkIndexJournal)]) == 1
        assert len([o for o in added if isinstance(o, TextChunk)]) == 3
