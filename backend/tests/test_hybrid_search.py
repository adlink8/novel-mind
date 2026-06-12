"""
混合搜索单元测试

测试 HybridSearchService 的各个方法：
- BM25 全文搜索
- 向量搜索
- 融合排序（hybrid_rerank）
- 全局搜索（search_global）
- 小说内搜索（search_novel）
- API 端点

由于测试环境使用 SQLite（不支持 tsvector），BM25 搜索使用 mock：
- bm25_search 通过 mock 返回预设结果
- vector_search 通过 mock ChromaDB 返回预设结果
- hybrid_rerank 使用真实数据进行融合排序测试
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# mock 掉 chromadb，避免未安装时 import 失败
sys.modules.setdefault("chromadb", MagicMock())
sys.modules.setdefault("chromadb.config", MagicMock())

from app.services.hybrid_search import HybridSearchService  # noqa: E402


# ─────────── 辅助数据 ───────────

def _make_bm25_result(
    chunk_id: int,
    novel_id: int = 1,
    novel_title: str = "龙族",
    chapter_id: int = 5,
    chapter_title: str = "第五幕",
    chunk_index: int = 0,
    content_snippet: str = "<mark>龙影</mark>笼罩着大地",
    score: float = 0.88,
) -> dict:
    """构建 BM25 搜索结果"""
    return {
        "novel_id": novel_id,
        "novel_title": novel_title,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "content_snippet": content_snippet,
        "score": score,
    }


def _make_vector_result(
    chunk_id: int,
    chapter_id: int = 5,
    chunk_index: int = 0,
    content: str = "第一章的内容",
    score: float = 0.82,
) -> dict:
    """构建向量搜索结果"""
    return {
        "chunk_id": chunk_id,
        "content": content,
        "score": score,
        "chapter_id": chapter_id,
        "chunk_index": chunk_index,
        "novel_id": 1,
    }


# ─────────── BM25 搜索测试 ───────────


class TestBm25Search:
    """测试 BM25 全文搜索"""

    @pytest.mark.asyncio
    async def test_bm25_search_basic(self):
        """基本全文搜索返回正确结果"""
        service = HybridSearchService()

        mock_db = AsyncMock()
        captured_params = {}

        async def capture_execute(sql, params, **kwargs):
            captured_params.update(params)
            mock_result = MagicMock()
            mock_row = MagicMock()
            mock_row.novel_id = 1
            mock_row.novel_title = "龙族"
            mock_row.chapter_id = 5
            mock_row.chapter_title = "第五幕 龙影"
            mock_row.chunk_id = 42
            mock_row.chunk_index = 5
            mock_row.headline = "在<mark>龙影</mark>笼罩下，少年缓缓抬头"
            mock_row.rank = 0.88
            mock_result.fetchall.return_value = [mock_row]
            return mock_result

        mock_db.execute = capture_execute

        results = await service._bm25_search(
            mock_db, query="龙影", novel_id=1, limit=20
        )

        assert len(results) == 1
        assert results[0]["chunk_id"] == 42
        assert results[0]["novel_title"] == "龙族"
        assert results[0]["chapter_title"] == "第五幕 龙影"
        assert results[0]["chunk_index"] == 5
        assert "mark" in results[0]["content_snippet"].lower()
        assert results[0]["score"] == 0.88

        # 验证 SQL 参数
        assert captured_params["novel_id"] == 1
        assert captured_params["query"] == "龙影"
        assert captured_params["limit"] == 20

    @pytest.mark.asyncio
    async def test_bm25_search_no_results(self):
        """无结果返回空列表"""
        service = HybridSearchService()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute.return_value = mock_result

        results = await service._bm25_search(
            mock_db, query="不存在的关键词", novel_id=999
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_bm25_search_global(self):
        """全局搜索不限制 novel_id"""
        service = HybridSearchService()

        mock_db = AsyncMock()
        captured_params = {}

        async def capture_execute(sql, params, **kwargs):
            captured_params.update(params)
            mock_result = MagicMock()
            mock_result.fetchall.return_value = []
            return mock_result

        mock_db.execute = capture_execute

        results = await service._bm25_search(
            mock_db, query="关键词", novel_id=None, limit=20
        )

        assert results == []
        assert "novel_id" not in captured_params

    @pytest.mark.asyncio
    async def test_bm25_search_with_owner(self):
        """带 owner_id 限制搜索范围"""
        service = HybridSearchService()

        mock_db = AsyncMock()
        captured_params = {}

        async def capture_execute(sql, params, **kwargs):
            captured_params.update(params)
            mock_result = MagicMock()
            mock_result.fetchall.return_value = []
            return mock_result

        mock_db.execute = capture_execute

        await service._bm25_search(
            mock_db, query="关键词", novel_id=None, owner_id=42
        )

        assert captured_params["owner_id"] == 42

    @pytest.mark.asyncio
    async def test_bm25_search_handles_error(self):
        """搜索失败时返回空列表（不抛异常）"""
        service = HybridSearchService()

        mock_db = AsyncMock()
        mock_db.execute.side_effect = RuntimeError("connection refused")

        results = await service._bm25_search(mock_db, query="test")
        assert results == []


# ─────────── 混合融合排序测试 ───────────


class TestHybridRerank:
    """测试 hybrid_rerank 融合排序"""

    @pytest.mark.asyncio
    async def test_hybrid_rerank_combines_scores(self):
        """融合排序正确合并 BM25 和向量分数"""
        service = HybridSearchService()

        bm25_results = [
            _make_bm25_result(chunk_id=1, score=0.8),
            _make_bm25_result(chunk_id=2, score=0.4),
        ]
        vector_results = [
            _make_vector_result(chunk_id=1, score=0.6),
            _make_vector_result(chunk_id=3, score=0.9),
        ]

        merged = await service._hybrid_rerank(bm25_results, vector_results, top_k=10)

        # 应有 3 条（chunk 1 合并，chunk 2 仅 BM25，chunk 3 仅向量）
        assert len(merged) == 3

        # chunk_id=1: bm25=0.8/0.8=1.0, vector=0.6/0.9≈0.667, final=0.5*1+0.5*0.667≈0.8333
        chunk1 = next(r for r in merged if r["chunk_id"] == 1)
        assert chunk1["bm25_score"] == pytest.approx(1.0)
        assert chunk1["vector_score"] == pytest.approx(0.6 / 0.9)
        assert chunk1["score"] == pytest.approx(0.5 * 1.0 + 0.5 * (0.6 / 0.9), rel=1e-3)

        # chunk_id=2: bm25=0.4/0.8=0.5, vector=0.0, final=0.25
        chunk2 = next(r for r in merged if r["chunk_id"] == 2)
        assert chunk2["bm25_score"] == pytest.approx(0.5)
        assert chunk2["vector_score"] == 0.0
        assert chunk2["score"] == pytest.approx(0.25)

        # chunk_id=3: bm25=0.0, vector=0.9/0.9=1.0, final=0.5
        chunk3 = next(r for r in merged if r["chunk_id"] == 3)
        assert chunk3["bm25_score"] == 0.0
        assert chunk3["vector_score"] == pytest.approx(1.0)
        assert chunk3["score"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_hybrid_rerank_dedup(self):
        """同一 chunk_id 在 BM25 和向量中均出现时合并分数"""
        service = HybridSearchService()

        bm25_results = [
            _make_bm25_result(chunk_id=1, score=0.9, content_snippet="a"),
            _make_bm25_result(chunk_id=1, score=0.7, content_snippet="longer text here"),
        ]
        vector_results = [
            _make_vector_result(chunk_id=1, score=0.8),
            _make_vector_result(chunk_id=1, score=0.6),
        ]

        merged = await service._hybrid_rerank(bm25_results, vector_results, top_k=10)

        assert len(merged) == 1
        assert merged[0]["chunk_id"] == 1
        # BM25 取最大值 0.9 → 归一化=1.0
        assert merged[0]["bm25_score"] == pytest.approx(1.0)
        # 向量取最大值 0.8 → 归一化=1.0
        assert merged[0]["vector_score"] == pytest.approx(1.0)
        # 保留更长的 snippet
        assert merged[0]["content_snippet"] == "longer text here"

    @pytest.mark.asyncio
    async def test_hybrid_rerank_empty_bm25(self):
        """仅有向量结果时正常返回"""
        service = HybridSearchService()

        vector_results = [
            _make_vector_result(chunk_id=1, score=0.7),
            _make_vector_result(chunk_id=2, score=0.3),
        ]

        merged = await service._hybrid_rerank([], vector_results, top_k=10)

        assert len(merged) == 2
        assert merged[0]["vector_score"] == pytest.approx(1.0)  # 归一化
        assert merged[1]["vector_score"] == pytest.approx(0.3 / 0.7)

    @pytest.mark.asyncio
    async def test_hybrid_rerank_empty_vector(self):
        """仅有 BM25 结果时正常返回"""
        service = HybridSearchService()

        bm25_results = [
            _make_bm25_result(chunk_id=1, score=0.5),
        ]

        merged = await service._hybrid_rerank(bm25_results, [], top_k=10)

        assert len(merged) == 1
        assert merged[0]["bm25_score"] == pytest.approx(1.0)
        assert merged[0]["vector_score"] == 0.0
        assert merged[0]["score"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_hybrid_rerank_top_k(self):
        """限制返回 top_k 条结果"""
        service = HybridSearchService()

        bm25_results = [
            _make_bm25_result(chunk_id=i, score=0.1 * i) for i in range(1, 6)
        ]

        merged = await service._hybrid_rerank(bm25_results, [], top_k=3)

        assert len(merged) == 3
        # 按分数降序排列
        assert merged[0]["chunk_id"] == 5

    @pytest.mark.asyncio
    async def test_hybrid_rerank_both_empty(self):
        """两个结果都为空返回空列表"""
        service = HybridSearchService()
        merged = await service._hybrid_rerank([], [], top_k=10)
        assert merged == []


# ─────────── 搜索入口测试 ───────────


class TestSearchNovel:
    """测试小说内混合搜索"""

    @pytest.mark.asyncio
    async def test_search_novel_combines_both(self):
        """小说内搜索正确融合 BM25 + 向量结果"""
        service = HybridSearchService()

        async def mock_bm25(*args, **kwargs):
            return [
                _make_bm25_result(chunk_id=1, score=0.8),
            ]

        async def mock_vector(*args, **kwargs):
            return [
                _make_vector_result(chunk_id=2, score=0.6),
            ]

        service._bm25_search = mock_bm25
        service._vector_search = mock_vector

        mock_db = AsyncMock()
        results = await service.search_novel(mock_db, novel_id=1, query="test")

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_novel_vector_fails_gracefully(self):
        """向量搜索失败时仍返回 BM25 结果"""
        service = HybridSearchService()

        async def mock_bm25(*args, **kwargs):
            return [
                _make_bm25_result(chunk_id=1, score=0.9),
            ]

        async def mock_vector(*args, **kwargs):
            raise RuntimeError("ChromaDB unavailable")

        service._bm25_search = mock_bm25
        service._vector_search = mock_vector

        mock_db = AsyncMock()
        results = await service.search_novel(mock_db, novel_id=1, query="test")

        assert len(results) == 1
        assert results[0]["chunk_id"] == 1
        assert results[0]["bm25_score"] == pytest.approx(1.0)


class TestSearchGlobal:
    """测试全局混合搜索"""

    @pytest.mark.asyncio
    async def test_search_global_aggregates_across_novels(self):
        """全局搜索跨多个小说聚合结果"""
        service = HybridSearchService()

        async def mock_bm25(*args, **kwargs):
            return [
                _make_bm25_result(chunk_id=1, novel_id=1, score=0.9),
                _make_bm25_result(chunk_id=10, novel_id=2, score=0.7, content_snippet="another"),
            ]

        async def mock_vector(*args, **kwargs):
            return [
                _make_vector_result(chunk_id=2, score=0.5),
            ]

        service._bm25_search = mock_bm25
        service._vector_search = mock_vector

        mock_db = AsyncMock()
        results = await service.search_global(mock_db, query="test", owner_id=1)

        # novel_id=1 和 novel_id=2 的结果都被聚合
        novel_ids = {r["novel_id"] for r in results}
        assert novel_ids == {1, 2}

    @pytest.mark.asyncio
    async def test_search_global_no_bm25_results(self):
        """BM25 无结果返回空列表"""
        service = HybridSearchService()

        async def mock_bm25(*args, **kwargs):
            return []

        service._bm25_search = mock_bm25

        mock_db = AsyncMock()
        results = await service.search_global(mock_db, query="test")

        assert results == []


# ─────────── API 端点测试 ───────────


class TestSearchApi:
    """测试搜索 API 端点"""

    @pytest.mark.asyncio
    async def test_global_search_success(self, auth_client):
        """全局搜索返回结果"""
        with patch(
            "app.services.hybrid_search.hybrid_search_service.search_global",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = [
                {
                    "novel_id": 1,
                    "novel_title": "龙族",
                    "chapter_id": 5,
                    "chapter_title": "第五幕 龙影",
                    "chunk_id": 42,
                    "chunk_index": 5,
                    "content_snippet": "在<mark>龙影</mark>笼罩下",
                    "score": 0.85,
                    "vector_score": 0.82,
                    "bm25_score": 0.88,
                }
            ]

            resp = await auth_client.post(
                "/api/search",
                json={"query": "龙影", "top_k": 10},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["query"] == "龙影"
            assert len(data["results"]) == 1
            result = data["results"][0]
            assert result["novel_title"] == "龙族"
            assert result["chunk_id"] == 42
            assert result["score"] == 0.85
            assert result["vector_score"] == 0.82
            assert result["bm25_score"] == 0.88

            mock_search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_global_search_requires_auth(self, client):
        """未认证时全局搜索返回 401"""
        resp = await client.post(
            "/api/search",
            json={"query": "龙影", "top_k": 10},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_global_search_empty_query(self, auth_client):
        """空查询返回 422"""
        resp = await auth_client.post(
            "/api/search",
            json={"query": "", "top_k": 10},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_global_search_top_k_exceeds(self, auth_client):
        """top_k 超过上限返回 422"""
        resp = await auth_client.post(
            "/api/search",
            json={"query": "test", "top_k": 100},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_novel_search_success(self, auth_client):
        """小说内搜索返回结果"""
        import io

        # 上传测试小说
        content = "第一章 初入江湖\n\n这是第一章的内容。\n\n第二章 拜师学艺\n\n这是第二章的内容。\n"
        resp = await auth_client.post(
            "/api/novels/upload",
            files={"file": ("search_test.txt", io.BytesIO(content.encode("utf-8")), "text/plain")},
        )
        assert resp.status_code == 200
        novel_id = resp.json()["id"]

        with patch(
            "app.services.hybrid_search.hybrid_search_service.search_novel",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = [
                {
                    "novel_id": novel_id,
                    "novel_title": "search_test",
                    "chapter_id": 1,
                    "chapter_title": "第一章",
                    "chunk_id": 1,
                    "chunk_index": 0,
                    "content_snippet": "这是<mark>第一章</mark>的内容",
                    "score": 0.95,
                    "vector_score": 0.92,
                    "bm25_score": 0.98,
                }
            ]

            resp = await auth_client.post(
                f"/api/search/novels/{novel_id}",
                json={"query": "第一章", "top_k": 10},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["results"][0]["chunk_id"] == 1
            assert data["results"][0]["score"] == 0.95

            mock_search.assert_awaited_once_with(
                mock_search.call_args.args[0],
                novel_id=novel_id,
                query="第一章",
                top_k=10,
            )

    @pytest.mark.asyncio
    async def test_novel_search_not_found(self, auth_client):
        """搜索不存在的小说返回 404"""
        resp = await auth_client.post(
            "/api/search/novels/9999",
            json={"query": "test", "top_k": 10},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_novel_search_no_auth(self, client):
        """无认证时小说内搜索可用（可选认证）"""
        # 小说不存在返回 404，而非 401
        resp = await client.post(
            "/api/search/novels/1",
            json={"query": "test", "top_k": 10},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_novel_search_empty_results(self, auth_client):
        """无结果时返回空列表"""
        import io

        content = "第一章 初入江湖\n\n这是第一章的内容。\n"
        resp = await auth_client.post(
            "/api/novels/upload",
            files={"file": ("empty_test.txt", io.BytesIO(content.encode("utf-8")), "text/plain")},
        )
        assert resp.status_code == 200
        novel_id = resp.json()["id"]

        with patch(
            "app.services.hybrid_search.hybrid_search_service.search_novel",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = []

            resp = await auth_client.post(
                f"/api/search/novels/{novel_id}",
                json={"query": "不存在的内容", "top_k": 10},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 0
            assert data["results"] == []
            assert data["query"] == "不存在的内容"
