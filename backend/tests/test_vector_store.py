"""
VectorStore 单元测试

使用 mock 隔离 ChromaDB 依赖，不依赖真实服务。
在模块加载前通过 sys.modules 注入 mock 的 chromadb，避免安装依赖。

覆盖范围:
- add_chunks: 批量写入调用正确性
- search: 返回格式与分数计算
- delete_novel_chunks: 集合删除
- get_chunk_count: 数量查询
- update_chunk_status: 状态更新
- 空集合处理
- 过滤条件传递
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# 在 import vector_store 之前，mock 掉 chromadb 模块
# 这样即使没有安装 chromadb 也能运行测试
_mock_chromadb = MagicMock()
sys.modules.setdefault("chromadb", _mock_chromadb)

from app.services.vector_store import VectorStore, VectorStoreError  # noqa: E402


@pytest.fixture
def mock_chroma_client():
    """创建 mock 的 ChromaDB HttpClient"""
    with patch("app.services.vector_store.chromadb.HttpClient") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        yield client


@pytest.fixture
def vector_store(mock_chroma_client):
    """创建注入 mock 客户端的 VectorStore 实例"""
    return VectorStore(host="localhost", port=8001)


@pytest.fixture
def mock_collection():
    """创建 mock 的 Collection 对象"""
    collection = MagicMock()
    collection.count.return_value = 0
    return collection


# ── add_chunks 测试 ──


class TestAddChunks:
    """测试 add_chunks 批量写入"""

    @pytest.mark.asyncio
    async def test_add_chunks_calls_correctly(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """验证 add_chunks 调用 collection.add 的参数格式"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection

        chunks = [
            {
                "id": 1,
                "content": "第一段文本",
                "embedding": [0.1, 0.2, 0.3],
                "metadata": {
                    "chapter_id": 1,
                    "chunk_index": 0,
                    "chunk_type": "paragraph",
                    "word_count": 100,
                },
            },
            {
                "id": 2,
                "content": "第二段文本",
                "embedding": [0.4, 0.5, 0.6],
                "metadata": {
                    "chapter_id": 1,
                    "chunk_index": 1,
                    "chunk_type": "dialogue",
                    "word_count": 80,
                },
            },
        ]

        await vector_store.add_chunks(novel_id=1, chunks=chunks)

        mock_chroma_client.get_or_create_collection.assert_called_once_with(
            name="novel_1", metadata={"hnsw:space": "cosine"}
        )
        mock_collection.add.assert_called_once_with(
            ids=["chunk_1", "chunk_2"],
            documents=["第一段文本", "第二段文本"],
            embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            metadatas=[
                {"chapter_id": 1, "chunk_index": 0, "chunk_type": "paragraph", "word_count": 100},
                {"chapter_id": 1, "chunk_index": 1, "chunk_type": "dialogue", "word_count": 80},
            ],
        )

    @pytest.mark.asyncio
    async def test_add_chunks_empty_list(
        self, vector_store, mock_chroma_client
    ):
        """空列表不触发任何调用"""
        await vector_store.add_chunks(novel_id=1, chunks=[])
        mock_chroma_client.get_or_create_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_chunks_raises_on_error(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """写入失败时抛出 VectorStoreError"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.add.side_effect = RuntimeError("connection refused")

        with pytest.raises(VectorStoreError, match="写入向量失败"):
            await vector_store.add_chunks(
                novel_id=1,
                chunks=[{"id": 1, "content": "text", "embedding": [0.1], "metadata": {}}],
            )


# ── search 测试 ──


class TestSearch:
    """测试 search 语义搜索"""

    @pytest.mark.asyncio
    async def test_search_returns_correct_format(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """验证 search 返回格式"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [["chunk_1", "chunk_2"]],
            "documents": [["文本一", "文本二"]],
            "distances": [[0.2, 0.5]],
            "metadatas": [[
                {"chapter_id": 1, "chunk_type": "paragraph"},
                {"chapter_id": 2, "chunk_type": "dialogue"},
            ]],
        }

        results = await vector_store.search(
            novel_id=1, query_embedding=[0.1, 0.2, 0.3], top_k=5
        )

        assert len(results) == 2
        assert results[0]["chunk_id"] == "chunk_1"
        assert results[0]["content"] == "文本一"
        assert results[0]["score"] == pytest.approx(0.8)  # 1 - 0.2
        assert results[0]["metadata"]["chapter_id"] == 1
        assert results[1]["score"] == pytest.approx(0.5)  # 1 - 0.5

    @pytest.mark.asyncio
    async def test_search_empty_result(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """空集合返回空列表"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "distances": [[]],
            "metadatas": [[]],
        }

        results = await vector_store.search(
            novel_id=99, query_embedding=[0.1, 0.2, 0.3]
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_search_passes_filters(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """验证过滤条件正确传递"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [["chunk_5"]],
            "documents": [["对话文本"]],
            "distances": [[0.1]],
            "metadatas": [[{"chunk_type": "dialogue"}]],
        }

        await vector_store.search(
            novel_id=1,
            query_embedding=[0.1],
            top_k=3,
            filters={"chunk_type": "dialogue"},
        )

        mock_collection.query.assert_called_once_with(
            query_embeddings=[[0.1]],
            n_results=3,
            where={"chunk_type": "dialogue"},
        )

    @pytest.mark.asyncio
    async def test_search_no_filters(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """无过滤条件时不传递 where 参数"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "distances": [[]],
            "metadatas": [[]],
        }

        await vector_store.search(
            novel_id=1, query_embedding=[0.1], top_k=5, filters=None
        )

        call_kwargs = mock_collection.query.call_args[1]
        assert "where" not in call_kwargs

    @pytest.mark.asyncio
    async def test_search_score_clamping(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """分数被限制在 0-1 范围内"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.query.return_value = {
            "ids": [["chunk_1"]],
            "documents": [["文本"]],
            "distances": [[1.5]],  # 超出范围的距离
            "metadatas": [[{}]],
        }

        results = await vector_store.search(
            novel_id=1, query_embedding=[0.1]
        )
        # score = 1 - 1.5 = -0.5，被 clamp 到 0
        assert results[0]["score"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_search_raises_on_error(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """搜索失败时抛出 VectorStoreError"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.query.side_effect = RuntimeError("timeout")

        with pytest.raises(VectorStoreError, match="向量搜索失败"):
            await vector_store.search(novel_id=1, query_embedding=[0.1])


# ── delete_novel_chunks 测试 ──


class TestDeleteNovelChunks:
    """测试 delete_novel_chunks 集合删除"""

    @pytest.mark.asyncio
    async def test_delete_calls_correctly(
        self, vector_store, mock_chroma_client
    ):
        """验证调用 delete_collection 且集合名正确"""
        await vector_store.delete_novel_chunks(novel_id=42)
        mock_chroma_client.delete_collection.assert_called_once_with(name="novel_42")

    @pytest.mark.asyncio
    async def test_delete_raises_on_error(
        self, vector_store, mock_chroma_client
    ):
        """删除失败时抛出 VectorStoreError"""
        mock_chroma_client.delete_collection.side_effect = RuntimeError("not found")

        with pytest.raises(VectorStoreError, match="删除向量集合失败"):
            await vector_store.delete_novel_chunks(novel_id=1)


# ── get_chunk_count 测试 ──


class TestGetChunkCount:
    """测试 get_chunk_count 数量查询"""

    @pytest.mark.asyncio
    async def test_count_returns_number(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """正常返回向量数量"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.count.return_value = 42

        count = await vector_store.get_chunk_count(novel_id=1)
        assert count == 42

    @pytest.mark.asyncio
    async def test_count_empty_collection(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """空集合返回 0"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.count.return_value = 0

        count = await vector_store.get_chunk_count(novel_id=1)
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_returns_zero_on_error(
        self, vector_store, mock_chroma_client
    ):
        """集合不存在或其他错误时返回 0（不抛异常）"""
        mock_chroma_client.get_or_create_collection.side_effect = RuntimeError(
            "collection not found"
        )

        count = await vector_store.get_chunk_count(novel_id=999)
        assert count == 0


# ── update_chunk_status 测试 ──


class TestUpdateChunkStatus:
    """测试 update_chunk_status 状态更新"""

    @pytest.mark.asyncio
    async def test_update_calls_correctly(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """验证 update 调用参数正确"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection

        await vector_store.update_chunk_status(
            novel_id=1, chunk_id=10, status="embedded"
        )

        mock_collection.update.assert_called_once_with(
            ids=["chunk_10"],
            metadatas=[{"embedding_status": "embedded"}],
        )

    @pytest.mark.asyncio
    async def test_update_status_pending(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """支持 pending 状态"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection

        await vector_store.update_chunk_status(
            novel_id=1, chunk_id=5, status="pending"
        )

        mock_collection.update.assert_called_once_with(
            ids=["chunk_5"],
            metadatas=[{"embedding_status": "pending"}],
        )

    @pytest.mark.asyncio
    async def test_update_status_failed(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """支持 failed 状态"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection

        await vector_store.update_chunk_status(
            novel_id=1, chunk_id=3, status="failed"
        )

        mock_collection.update.assert_called_once_with(
            ids=["chunk_3"],
            metadatas=[{"embedding_status": "failed"}],
        )

    @pytest.mark.asyncio
    async def test_update_raises_on_error(
        self, vector_store, mock_chroma_client, mock_collection
    ):
        """更新失败时抛出 VectorStoreError"""
        mock_chroma_client.get_or_create_collection.return_value = mock_collection
        mock_collection.update.side_effect = RuntimeError("update failed")

        with pytest.raises(VectorStoreError, match="更新块状态失败"):
            await vector_store.update_chunk_status(
                novel_id=1, chunk_id=1, status="embedded"
            )


# ── 初始化测试 ──


class TestInit:
    """测试 VectorStore 初始化"""

    def test_init_creates_http_client(self):
        """验证初始化创建 HttpClient"""
        with patch("app.services.vector_store.chromadb.HttpClient") as mock_cls:
            VectorStore(host="chroma-host", port=9000)
            mock_cls.assert_called_once_with(host="chroma-host", port=9000)

    def test_init_default_params(self):
        """验证默认参数"""
        with patch("app.services.vector_store.chromadb.HttpClient") as mock_cls:
            VectorStore()
            mock_cls.assert_called_once_with(host="localhost", port=8001)
