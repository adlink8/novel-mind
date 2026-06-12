"""
ChromaDB 向量存储服务

基于 ChromaDB 的向量存储，用于小说文本块的 embedding 索引和语义搜索。

核心功能:
  - add_chunks()          : 批量写入文本块向量
  - search()              : 语义搜索（余弦相似度）
  - delete_novel_chunks() : 删除指定小说的全部向量
  - get_chunk_count()     : 查询向量数量
  - update_chunk_status() : 更新块的 embedding 状态

架构:
  每个小说对应一个 ChromaDB Collection（命名: novel_{novel_id}），
  使用余弦距离（cosine）进行相似度计算。

使用方式:
  from app.services.vector_store import vector_store
  await vector_store.add_chunks(novel_id=1, chunks=[...])
  results = await vector_store.search(novel_id=1, query_embedding=[...])
"""

import asyncio
import logging
from typing import Any

import chromadb

logger = logging.getLogger(__name__)


class VectorStoreError(Exception):
    """向量存储操作异常基类"""


class VectorStore:
    """
    ChromaDB 向量存储客户端。

    通过 HTTP 连接 ChromaDB 服务，为每个小说维护独立的向量集合。
    所有方法均为异步，内部使用 asyncio.to_thread 包装同步调用。
    """

    def __init__(self, host: str = "localhost", port: int = 8001):
        """
        初始化 ChromaDB HTTP 客户端。

        Args:
            host: ChromaDB 服务地址
            port: ChromaDB 服务端口（docker-compose 中映射的宿主机端口）
        """
        self.client = chromadb.HttpClient(host=host, port=port)

    def _get_collection(self, novel_id: int):
        """
        获取或创建指定小说的向量集合。

        每个小说拥有独立的集合，使用余弦距离度量。

        Args:
            novel_id: 小说 ID

        Returns:
            ChromaDB Collection 对象
        """
        return self.client.get_or_create_collection(
            name=f"novel_{novel_id}",
            metadata={"hnsw:space": "cosine"},
        )

    async def add_chunks(self, novel_id: int, chunks: list[dict[str, Any]]) -> None:
        """
        批量写入文本块向量。

        Args:
            novel_id: 小说 ID
            chunks: 文本块列表，每个块包含:
                - id (int): 文本块 ID
                - content (str): 文本内容
                - embedding (list[float]): 向量数据
                - metadata (dict): 元数据，包含 chapter_id, chunk_index, chunk_type, word_count

        Raises:
            VectorStoreError: 写入失败时抛出

        示例:
            chunks = [{
                "id": 1,
                "content": "段落文本...",
                "embedding": [0.1, 0.2, ...],
                "metadata": {"chapter_id": 1, "chunk_index": 0, "chunk_type": "paragraph", "word_count": 350}
            }]
            await vector_store.add_chunks(novel_id=1, chunks=chunks)
        """
        if not chunks:
            return

        def _add():
            collection = self._get_collection(novel_id)
            ids = [f"chunk_{chunk['id']}" for chunk in chunks]
            documents = [chunk["content"] for chunk in chunks]
            embeddings = [chunk["embedding"] for chunk in chunks]
            metadatas = [chunk.get("metadata", {}) for chunk in chunks]
            collection.add(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )

        try:
            await asyncio.to_thread(_add)
            logger.info("已写入 %d 个向量到 novel_%d", len(chunks), novel_id)
        except Exception as e:
            logger.error("写入向量失败 novel_%d: %s", novel_id, e)
            raise VectorStoreError(f"写入向量失败: {e}") from e

    async def search(
        self,
        novel_id: int,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        语义搜索：根据查询向量检索最相似的文本块。

        Args:
            novel_id: 小说 ID
            query_embedding: 查询向量（与索引向量同维度）
            top_k: 返回结果数量上限
            filters: 过滤条件，支持按 chunk_type 过滤
                示例: {"chunk_type": "dialogue"}

        Returns:
            搜索结果列表，每项包含:
                - chunk_id (str): 文本块标识（如 "chunk_1"）
                - content (str): 文本内容
                - score (float): 余弦相似度（0-1，越大越相似）
                - metadata (dict): 元数据

        示例:
            results = await vector_store.search(
                novel_id=1,
                query_embedding=[0.1, 0.2, ...],
                top_k=5,
                filters={"chunk_type": "dialogue"}
            )
        """
        def _search():
            collection = self._get_collection(novel_id)

            # 构建 ChromaDB where 过滤条件
            where = None
            if filters:
                where = {}
                for key, value in filters.items():
                    where[key] = value

            kwargs: dict[str, Any] = {
                "query_embeddings": [query_embedding],
                "n_results": top_k,
            }
            if where:
                kwargs["where"] = where

            return collection.query(**kwargs)

        try:
            result = await asyncio.to_thread(_search)
        except Exception as e:
            logger.error("向量搜索失败 novel_%d: %s", novel_id, e)
            raise VectorStoreError(f"向量搜索失败: {e}") from e

        # 解析 ChromaDB 返回格式
        results = []
        if result and result["ids"] and result["ids"][0]:
            ids = result["ids"][0]
            documents = result["documents"][0] if result["documents"] else [None] * len(ids)
            distances = result["distances"][0] if result["distances"] else [0.0] * len(ids)
            metadatas = result["metadatas"][0] if result["metadatas"] else [{}] * len(ids)

            for i, chunk_id in enumerate(ids):
                # ChromaDB cosine distance = 1 - similarity
                score = 1.0 - distances[i] if distances[i] is not None else 0.0
                results.append({
                    "chunk_id": chunk_id,
                    "content": documents[i],
                    "score": max(0.0, min(1.0, score)),
                    "metadata": metadatas[i] or {},
                })

        return results

    async def delete_novel_chunks(self, novel_id: int) -> None:
        """
        删除指定小说的全部向量集合。

        Args:
            novel_id: 小说 ID

        Raises:
            VectorStoreError: 删除失败时抛出
        """
        def _delete():
            self.client.delete_collection(name=f"novel_{novel_id}")

        try:
            await asyncio.to_thread(_delete)
            logger.info("已删除 novel_%d 向量集合", novel_id)
        except Exception as e:
            logger.error("删除向量集合失败 novel_%d: %s", novel_id, e)
            raise VectorStoreError(f"删除向量集合失败: {e}") from e

    async def get_chunk_count(self, novel_id: int) -> int:
        """
        获取指定小说的向量数量。

        Args:
            novel_id: 小说 ID

        Returns:
            向量数量（集合不存在时返回 0）
        """
        def _count():
            try:
                collection = self._get_collection(novel_id)
                return collection.count()
            except Exception:
                return 0

        try:
            return await asyncio.to_thread(_count)
        except Exception as e:
            logger.error("查询向量数量失败 novel_%d: %s", novel_id, e)
            return 0

    async def update_chunk_status(
        self, novel_id: int, chunk_id: int, status: str
    ) -> None:
        """
        更新文本块的 embedding 状态。

        通过更新 metadata 中的 embedding_status 字段实现状态追踪。

        Args:
            novel_id: 小说 ID
            chunk_id: 文本块 ID
            status: 新状态（pending / embedded / failed）

        Raises:
            VectorStoreError: 更新失败时抛出
        """

        def _update():
            collection = self._get_collection(novel_id)
            collection.update(
                ids=[f"chunk_{chunk_id}"],
                metadatas=[{"embedding_status": status}],
            )

        try:
            await asyncio.to_thread(_update)
            logger.debug(
                "已更新 chunk_%d 状态为 %s (novel_%d)", chunk_id, status, novel_id
            )
        except Exception as e:
            logger.error("更新块状态失败 chunk_%d: %s", chunk_id, e)
            raise VectorStoreError(f"更新块状态失败: {e}") from e


# 全局单例，整个应用共享同一个 VectorStore 实例
vector_store = VectorStore()
