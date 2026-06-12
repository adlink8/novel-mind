"""
IndexingService 单元测试

使用 mock 隔离外部依赖（数据库、AI 服务、向量存储），
不依赖真实服务即可验证索引管线逻辑。

覆盖范围:
- index_novel 完整流程
- search_similar 流程
- 批量 embedding (_batch_embed)
- 错误处理（单块失败、批量失败）
- 进度回调
- 状态更新
- 边界情况（小说不存在、无章节、空分块）
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# mock 掉 chromadb，避免未安装时 import 失败
_mock_chromadb = MagicMock()
sys.modules.setdefault("chromadb", _mock_chromadb)

# mock 掉 litellm，避免未安装时 import 失败
_mock_litellm = MagicMock()
sys.modules.setdefault("litellm", _mock_litellm)

from app.services.indexing_service import IndexingService, IndexingError  # noqa: E402


# ── Fixtures ──


@pytest.fixture
def mock_db():
    """创建 mock 的 AsyncSession"""
    db = AsyncMock()
    db.get = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def mock_novel():
    """创建 mock 的 Novel 对象"""
    novel = MagicMock()
    novel.id = 1
    novel.title = "测试小说"
    novel.status = "importing"
    return novel


@pytest.fixture
def mock_chapters():
    """创建 mock 的 Chapter 列表"""
    chapters = []
    for i in range(1, 4):
        ch = MagicMock()
        ch.id = i
        ch.chapter_number = i
        ch.content = f"第{i}章内容。" * 100
        chapters.append(ch)
    return chapters


@pytest.fixture
def mock_text_chunks():
    """创建 mock 的 TextChunk 记录列表"""
    chunks = []
    for i in range(1, 4):
        chunk = MagicMock()
        chunk.id = i
        chunk.novel_id = 1
        chunk.chapter_id = (i - 1) // 2 + 1
        chunk.chunk_index = i - 1
        chunk.content = f"第{i}个文本块的内容"
        chunk.chunk_type = "paragraph"
        chunk.word_count = 20
        chunk.metadata_json = {"chapter_id": chunk.chapter_id}
        chunk.embedding_status = "pending"
        chunks.append(chunk)
    return chunks


@pytest.fixture
def mock_chunking_service():
    """创建 mock 的 ChunkingService"""
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
    """创建 mock 的 VectorStore"""
    store = AsyncMock()
    store.add_chunks = AsyncMock()
    store.search = AsyncMock(
        return_value=[
            {
                "chunk_id": "chunk_1",
                "content": "匹配的文本内容",
                "score": 0.85,
                "metadata": {
                    "chapter_id": 1,
                    "chunk_index": 0,
                    "chunk_type": "paragraph",
                },
            }
        ]
    )
    return store


@pytest.fixture
def indexing_service(mock_chunking_service, mock_vector_store):
    """创建注入 mock 依赖的 IndexingService 实例"""
    service = IndexingService()
    service.chunking_service = mock_chunking_service
    service.vector_store = mock_vector_store
    return service


# ── index_novel 完整流程测试 ──


class TestIndexNovel:
    """测试 index_novel 完整索引流程"""

    @pytest.mark.asyncio
    async def test_full_index_flow(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
        mock_vector_store,
    ):
        """完整索引流程：chunking -> embedding -> ready"""
        # 配置数据库 mock
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        # mock AI embedding
        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(
                return_value=[[0.1, 0.2, 0.3]] * 3
            )

            result = await indexing_service.index_novel(mock_db, novel_id=1)

        # 验证结果
        assert result["novel_id"] == 1
        assert result["total_chunks"] == 3
        assert result["embedded_chunks"] == 3
        assert result["failed_chunks"] == 0
        assert result["status"] == "ready"

        # 验证最终状态为 ready
        assert mock_novel.status == "ready"

        # 验证 chunking_service 被调用
        mock_chunking_service.chunk_novel.assert_called_once()

        # 验证向量写入
        mock_vector_store.add_chunks.assert_called_once()

    @pytest.mark.asyncio
    async def test_novel_not_found(self, indexing_service, mock_db):
        """小说不存在时抛出 IndexingError"""
        mock_db.get.return_value = None

        with pytest.raises(IndexingError, match="小说不存在"):
            await indexing_service.index_novel(mock_db, novel_id=999)

    @pytest.mark.asyncio
    async def test_no_chapters(self, indexing_service, mock_db, mock_novel):
        """没有章节时抛出 IndexingError"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        with pytest.raises(IndexingError, match="没有章节"):
            await indexing_service.index_novel(mock_db, novel_id=1)

    @pytest.mark.asyncio
    async def test_empty_chunks_after_chunking(
        self, indexing_service, mock_db, mock_novel, mock_chapters, mock_chunking_service
    ):
        """分块结果为空时直接标记为 ready"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        mock_chunking_service.chunk_novel.return_value = []

        result = await indexing_service.index_novel(mock_db, novel_id=1)

        assert result["total_chunks"] == 0
        assert result["embedded_chunks"] == 0
        assert result["failed_chunks"] == 0
        assert result["status"] == "ready"

    @pytest.mark.asyncio
    async def test_status_transitions(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
    ):
        """验证状态流转: chunking -> embedding -> ready"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        status_history = []
        original_setattr = type(mock_novel).__setattr__

        def track_status(self_obj, name, value):
            if name == "status":
                status_history.append(value)
            original_setattr(self_obj, name, value)

        type(mock_novel).__setattr__ = track_status

        try:
            with patch("app.services.indexing_service.ai_service") as mock_ai:
                mock_ai.embedding = AsyncMock(return_value=[[0.1, 0.2, 0.3]] * 3)
                await indexing_service.index_novel(mock_db, novel_id=1)
        finally:
            type(mock_novel).__setattr__ = original_setattr

        assert "chunking" in status_history
        assert "embedding" in status_history
        assert "ready" in status_history


# ── search_similar 测试 ──


class TestSearchSimilar:
    """测试 search_similar 语义搜索流程"""

    @pytest.mark.asyncio
    async def test_basic_search(self, indexing_service, mock_db, mock_vector_store):
        """基本搜索流程：生成查询向量 -> 检索 -> 返回结果"""
        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

            results = await indexing_service.search_similar(
                mock_db, novel_id=1, query="主角的性格", top_k=5
            )

        # 验证 AI embedding 被调用
        mock_ai.embedding.assert_called_once_with(texts=["主角的性格"])

        # 验证向量搜索被调用
        mock_vector_store.search.assert_called_once()

        # 验证返回格式
        assert len(results) == 1
        assert results[0]["chunk_id"] == 1
        assert results[0]["content"] == "匹配的文本内容"
        assert results[0]["score"] == 0.85
        assert results[0]["chapter_id"] == 1
        assert results[0]["chunk_index"] == 0
        assert results[0]["chunk_type"] == "paragraph"

    @pytest.mark.asyncio
    async def test_search_with_single_chunk_type(
        self, indexing_service, mock_db, mock_vector_store
    ):
        """单类型过滤：直接传递给 vector_store"""
        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]])

            await indexing_service.search_similar(
                mock_db,
                novel_id=1,
                query="测试",
                chunk_types=["dialogue"],
            )

        call_kwargs = mock_vector_store.search.call_args[1]
        assert call_kwargs["filters"] == {"chunk_type": "dialogue"}

    @pytest.mark.asyncio
    async def test_search_with_multiple_chunk_types(
        self, indexing_service, mock_db, mock_vector_store
    ):
        """多类型过滤：在返回阶段过滤结果"""
        # 返回包含不同类型的结果
        mock_vector_store.search.return_value = [
            {
                "chunk_id": "chunk_1",
                "content": "对话内容",
                "score": 0.9,
                "metadata": {"chapter_id": 1, "chunk_index": 0, "chunk_type": "dialogue"},
            },
            {
                "chunk_id": "chunk_2",
                "content": "描写内容",
                "score": 0.8,
                "metadata": {"chapter_id": 1, "chunk_index": 1, "chunk_type": "description"},
            },
            {
                "chunk_id": "chunk_3",
                "content": "叙述内容",
                "score": 0.7,
                "metadata": {"chapter_id": 1, "chunk_index": 2, "chunk_type": "narration"},
            },
        ]

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]])

            results = await indexing_service.search_similar(
                mock_db,
                novel_id=1,
                query="测试",
                chunk_types=["dialogue", "narration"],
            )

        # 只保留 dialogue 和 narration 类型
        assert len(results) == 2
        types = {r["chunk_type"] for r in results}
        assert types == {"dialogue", "narration"}

    @pytest.mark.asyncio
    async def test_search_empty_results(self, indexing_service, mock_db, mock_vector_store):
        """无匹配结果时返回空列表"""
        mock_vector_store.search.return_value = []

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]])

            results = await indexing_service.search_similar(
                mock_db, novel_id=1, query="不存在的内容"
            )

        assert results == []


# ── 批量 embedding 测试 ──


class TestBatchEmbed:
    """测试 _batch_embed 批量向量化"""

    @pytest.mark.asyncio
    async def test_small_batch(self, indexing_service):
        """小批量（< 100）直接调用一次"""
        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(
                return_value=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
            )

            result = await indexing_service._batch_embed(["文本1", "文本2", "文本3"])

        assert len(result) == 3
        assert result[0] == [0.1, 0.2]
        assert result[2] == [0.5, 0.6]
        mock_ai.embedding.assert_called_once()

    @pytest.mark.asyncio
    async def test_large_batch_split(self, indexing_service):
        """大批量分批调用"""
        texts = [f"文本{i}" for i in range(250)]

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            # 每批返回对应数量的向量
            call_count = 0

            async def mock_embedding(texts, model=None):
                nonlocal call_count
                call_count += 1
                return [[0.1] for _ in texts]

            mock_ai.embedding = AsyncMock(side_effect=mock_embedding)

            result = await indexing_service._batch_embed(texts, batch_size=100)

        assert len(result) == 250
        assert call_count == 3  # 100 + 100 + 50

    @pytest.mark.asyncio
    async def test_batch_embed_error_propagation(self, indexing_service):
        """API 调用失败时异常向上传播"""
        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(side_effect=RuntimeError("API 超时"))

            with pytest.raises(RuntimeError, match="API 超时"):
                await indexing_service._batch_embed(["文本1"])

    @pytest.mark.asyncio
    async def test_empty_texts(self, indexing_service):
        """空文本列表返回空结果"""
        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[])

            result = await indexing_service._batch_embed([])

        assert result == []


# ── 错误处理测试 ──


class TestErrorHandling:
    """测试错误处理和错误隔离"""

    @pytest.mark.asyncio
    async def test_embedding_batch_failure_continues(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
        mock_vector_store,
    ):
        """单批 embedding 失败不影响其他批次"""
        # 生成 150 个块
        chunks = [
            {
                "content": f"块{i}",
                "chunk_type": "paragraph",
                "chunk_index": i,
                "word_count": 10,
                "metadata_json": {"chapter_id": 1},
            }
            for i in range(150)
        ]
        mock_chunking_service.chunk_novel.return_value = chunks

        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        call_count = 0

        async def mock_embedding(texts, model=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一批成功
                return [[0.1] for _ in texts]
            else:
                # 第二批失败
                raise RuntimeError("Rate limited")

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(side_effect=mock_embedding)

            result = await indexing_service.index_novel(mock_db, novel_id=1)

        assert result["total_chunks"] == 150
        assert result["embedded_chunks"] == 100
        assert result["failed_chunks"] == 50
        assert result["status"] == "partial"

    @pytest.mark.asyncio
    async def test_vector_store_failure_marks_failed(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
        mock_vector_store,
    ):
        """向量写入失败时标记为 failed"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        mock_vector_store.add_chunks.side_effect = RuntimeError("ChromaDB 不可用")

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1, 0.2, 0.3]] * 3)

            result = await indexing_service.index_novel(mock_db, novel_id=1)

        assert result["failed_chunks"] == 3
        assert result["embedded_chunks"] == 0
        assert result["status"] == "partial"

    @pytest.mark.asyncio
    async def test_all_embedding_failures(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
    ):
        """所有批次都失败时返回全部失败"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(
                side_effect=RuntimeError("服务不可用")
            )

            result = await indexing_service.index_novel(mock_db, novel_id=1)

        assert result["embedded_chunks"] == 0
        assert result["failed_chunks"] == 3
        assert result["status"] == "partial"


# ── 进度回调测试 ──


class TestProgressCallback:
    """测试进度回调机制"""

    @pytest.mark.asyncio
    async def test_callback_called_during_flow(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
        mock_vector_store,
    ):
        """索引过程中进度回调被正确调用"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        callback = AsyncMock()

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)

            await indexing_service.index_novel(
                mock_db, novel_id=1, progress_callback=callback
            )

        # 验证回调被调用多次
        assert callback.call_count >= 3

        # 验证第一次调用是 chunking 阶段
        first_call = callback.call_args_list[0]
        assert first_call.args == (1, 0, 0, "chunking")

        # 验证最后一次调用是 complete
        last_call = callback.call_args_list[-1]
        assert last_call.args[0] == 1
        assert last_call.args[2] == 3  # total
        assert last_call.args[3] == "ready"

    @pytest.mark.asyncio
    async def test_callback_not_required(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
    ):
        """progress_callback 为 None 时不报错"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)

            result = await indexing_service.index_novel(mock_db, novel_id=1)

        assert result["status"] == "ready"

    @pytest.mark.asyncio
    async def test_callback_shows_progress(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
    ):
        """回调中 current 值递增"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        progress_values = []

        async def track_progress(novel_id, current, total, status):
            progress_values.append((current, total, status))

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)

            await indexing_service.index_novel(
                mock_db, novel_id=1, progress_callback=track_progress
            )

        # 验证 embedding 阶段的 current 值
        embedding_progress = [
            p for p in progress_values if p[2] == "embedding"
        ]
        if embedding_progress:
            # 最后一次 embedding 回调的 current 应该等于 total
            last_embedding = embedding_progress[-1]
            assert last_embedding[0] == last_embedding[1]


# ── 状态更新测试 ──


class TestStatusUpdate:
    """测试数据库状态更新"""

    @pytest.mark.asyncio
    async def test_commit_called_multiple_times(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
    ):
        """验证 db.commit 被多次调用（状态流转时）"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)

            await indexing_service.index_novel(mock_db, novel_id=1)

        # commit 被多次调用：chunking -> 写入chunks -> embedding -> 最终状态
        assert mock_db.commit.call_count >= 3

    @pytest.mark.asyncio
    async def test_text_chunk_records_created(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
    ):
        """验证 TextChunk 记录被添加到数据库"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)

            await indexing_service.index_novel(mock_db, novel_id=1)

        # db.add 被调用了 3 次（每个 chunk 一次）
        assert mock_db.add.call_count == 3

    @pytest.mark.asyncio
    async def test_chunk_embedding_status_updated(
        self,
        indexing_service,
        mock_db,
        mock_novel,
        mock_chapters,
        mock_chunking_service,
        mock_vector_store,
    ):
        """验证 chunk 的 embedding_status 在流程中被更新"""
        mock_db.get.return_value = mock_novel
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_chapters
        mock_db.execute.return_value = mock_result

        added_records = []

        def capture_add(record):
            added_records.append(record)

        mock_db.add.side_effect = capture_add

        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1]] * 3)

            await indexing_service.index_novel(mock_db, novel_id=1)

        # 所有记录最终应为 embedded
        for record in added_records:
            assert record.embedding_status == "embedded"

    @pytest.mark.asyncio
    async def test_search_similar_calls_embedding_and_vector_search(
        self, indexing_service, mock_db, mock_vector_store
    ):
        """search_similar 验证调用链路"""
        with patch("app.services.indexing_service.ai_service") as mock_ai:
            mock_ai.embedding = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

            await indexing_service.search_similar(
                mock_db, novel_id=1, query="测试查询", top_k=3
            )

        # 验证 embedding 调用
        mock_ai.embedding.assert_called_once_with(texts=["测试查询"])

        # 验证 vector_store.search 调用参数
        search_call = mock_vector_store.search.call_args
        assert search_call[1]["novel_id"] == 1
        assert search_call[1]["query_embedding"] == [0.1, 0.2, 0.3]
        assert search_call[1]["top_k"] == 3


# ── 单例测试 ──


class TestSingleton:
    """测试全局单例"""

    def test_singleton_exists(self):
        """验证全局单例 indexing_service 存在"""
        from app.services.indexing_service import indexing_service as svc

        assert svc is not None
        assert isinstance(svc, IndexingService)
