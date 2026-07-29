"""
Embedding 索引管线服务

将小说文本分块后生成 embedding 向量并存储到 ChromaDB，
是 RAG 检索系统的核心编排层。

完整流程:
  1. 从数据库读取小说和章节
  2. 更新 Novel.status = "chunking"
  3. 调用 chunking_service.chunk_novel 分块
  4. 将分块写入 text_chunks 表
  5. 更新 Novel.status = "embedding"
  6. 批量调用 ai_service.embedding 生成向量
  7. 调用 vector_store.add_chunks 写入向量
  8. 更新 text_chunks.embedding_status = "embedded"
  9. 更新 Novel.status = "ready"
  10. 返回统计信息

使用方式:
  from app.services.indexing_service import indexing_service
  result = await indexing_service.index_novel(db, novel_id=1)
  results = await indexing_service.search_similar(db, novel_id=1, query="主角的性格")
"""

import asyncio
import logging
from typing import Any, Callable, Awaitable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.novel import Novel, Chapter
from app.models.text_chunk import TextChunk
from app.services.ai_service import ai_service
from app.services.chunking_service import ChunkingService
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# 进度回调类型: (novel_id, current, total, status)
ProgressCallback = Callable[[int, int, str], Awaitable[None]]


class IndexingError(Exception):
    """索引操作异常基类"""


class IndexingService:
    """
    Embedding 索引管线服务。

    编排分块、向量化、存储的完整流程，
    支持进度回调和单块级别的错误隔离。
    """

    def __init__(self):
        self.chunking_service = ChunkingService()
        self.vector_store = VectorStore()

    @staticmethod
    def _retry_settings() -> tuple[int, float]:
        """读取有限重试配置，并对非法环境值安全回退。"""
        try:
            retries = max(0, int(getattr(settings, "embedding_max_retries", 2)))
        except (TypeError, ValueError):
            retries = 2
        try:
            backoff = max(
                0.0,
                float(getattr(settings, "embedding_retry_backoff_seconds", 0.5)),
            )
        except (TypeError, ValueError):
            backoff = 0.5
        return retries, backoff

    async def _with_retry(self, operation, *, operation_name: str):
        """对 embedding 和向量写入执行有限指数退避重试。"""
        max_retries, backoff = self._retry_settings()
        for attempt in range(max_retries + 1):
            try:
                return await operation()
            except Exception:
                if attempt >= max_retries:
                    raise
                delay = backoff * (2**attempt)
                logger.warning(
                    "%s 失败，将在 %.2fs 后重试（%d/%d）",
                    operation_name,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                if delay:
                    await asyncio.sleep(delay)

    async def index_novel(
        self,
        db: AsyncSession,
        novel_id: int,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """
        完整的小说索引流程。

        Args:
            db: 数据库会话
            novel_id: 小说 ID
            progress_callback: 进度回调函数，签名为 (novel_id, current, total, status)

        Returns:
            统计信息字典，包含:
                - novel_id: 小说 ID
                - total_chunks: 总块数
                - embedded_chunks: 成功向量化的块数
                - failed_chunks: 失败的块数
                - status: 最终状态 ("ready" 或 "partial")；失败时 Novel.status 为 "indexing_failed"

        Raises:
            IndexingError: 流程级错误（如小说不存在、章节为空）
        """
        # 1. 读取小说和章节
        novel = await db.get(Novel, novel_id)
        if not novel:
            raise IndexingError(f"小说不存在: novel_id={novel_id}")

        from sqlalchemy.orm import undefer

        chapters_result = await db.execute(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.novel_id == novel_id)
            .order_by(Chapter.chapter_number)
        )
        chapters = list(chapters_result.scalars().all())

        if not chapters:
            raise IndexingError(f"小说没有章节内容: novel_id={novel_id}")

        # 在 commit 前取出正文，避免 deferred 列在 session 刷新后触发 MissingGreenlet
        chapter_payload = [
            {
                "id": ch.id,
                "chapter_number": ch.chapter_number,
                "content": ch.content or "",
            }
            for ch in chapters
        ]

        # 2. 更新状态为 chunking
        novel.status = "chunking"
        await db.commit()

        if progress_callback:
            await progress_callback(novel_id, 0, 0, "chunking")

        # 3. 分块
        from app.services.chunking_service import Chapter as ChunkChapter

        chunk_chapters = [
            ChunkChapter(
                id=item["id"],
                chapter_number=item["chapter_number"],
                content=item["content"],
            )
            for item in chapter_payload
        ]
        raw_chunks = await self.chunking_service.chunk_novel(
            novel_id=novel_id, chapters=chunk_chapters
        )

        if not raw_chunks:
            novel.status = "ready"
            await db.commit()
            return {
                "novel_id": novel_id,
                "total_chunks": 0,
                "embedded_chunks": 0,
                "failed_chunks": 0,
                "status": "ready",
            }

        # 4. 清理旧分块与旧向量（维度/模型切换后必须重建，避免 768/512 混用）
        from sqlalchemy import delete as sa_delete

        await db.execute(sa_delete(TextChunk).where(TextChunk.novel_id == novel_id))
        await db.commit()
        try:
            await self.vector_store.delete_novel_chunks(novel_id)
        except Exception as e:
            logger.warning(
                "清理旧 Chroma collection 失败 novel_%d: %s（将继续重建）", novel_id, e
            )

        # 5. 将分块写入 text_chunks 表
        chunk_records = []
        for chunk in raw_chunks:
            metadata = chunk.get("metadata_json", {})
            record = TextChunk(
                novel_id=novel_id,
                chapter_id=metadata.get("chapter_id"),
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
                chunk_type=chunk.get("chunk_type", "paragraph"),
                metadata_json=metadata if isinstance(metadata, dict) else {},
                word_count=chunk.get("word_count", len(chunk["content"])),
                embedding_status="pending",
            )
            db.add(record)
            chunk_records.append(record)

        await db.commit()

        # 刷新以获取自动生成的 id
        for record in chunk_records:
            await db.refresh(record)

        # 6. 更新状态为 embedding
        novel.status = "embedding"
        await db.commit()

        total = len(chunk_records)

        if progress_callback:
            await progress_callback(novel_id, 0, total, "embedding")

        # 7 & 8 & 9. 批量 embedding + 写入向量 + 更新状态
        embedded_count = 0
        failed_count = 0
        failed_chunk_ids: list[int] = []

        # local_st 可用更大 batch；ollama 仍用较小批避免超时
        batch_size = 100
        if getattr(settings, "embedding_provider", "").lower() in (
            "local_st",
            "local",
            "sentence_transformers",
            "bge",
        ):
            batch_size = max(
                16, int(getattr(settings, "embedding_batch_size", 64) or 64)
            )

        for i in range(0, total, batch_size):
            batch = chunk_records[i : i + batch_size]
            batch_texts = [r.content for r in batch]

            try:
                embeddings = await self._batch_embed(batch_texts)
            except Exception as e:
                logger.error(
                    "批量 embedding 失败 novel_%d batch_%d: %s",
                    novel_id,
                    i // batch_size,
                    e,
                )
                # 标记整个批次为失败
                for record in batch:
                    record.embedding_status = "failed"
                    failed_count += 1
                    failed_chunk_ids.append(record.id)
                await db.commit()
                if progress_callback:
                    await progress_callback(
                        novel_id, embedded_count, total, "embedding"
                    )
                continue

            # 构建向量写入格式
            chunks_for_store = []
            for j, record in enumerate(batch):
                record.embedding_status = "embedded"
                embedded_count += 1
                chunks_for_store.append(
                    {
                        "id": record.id,
                        "content": record.content,
                        "embedding": embeddings[j],
                        "metadata": {
                            "chapter_id": record.chapter_id,
                            "chunk_index": record.chunk_index,
                            "chunk_type": record.chunk_type,
                            "word_count": record.word_count,
                        },
                    }
                )

            # 写入 ChromaDB
            try:
                await self._with_retry(
                    lambda: self.vector_store.add_chunks(
                        novel_id=novel_id, chunks=chunks_for_store
                    ),
                    operation_name=f"Chroma 写入 batch {i // batch_size}",
                )
            except Exception as e:
                logger.error(
                    "写入向量失败 novel_%d batch_%d: %s",
                    novel_id,
                    i // 100,
                    e,
                )
                # 回退 embedding_status
                for record in batch:
                    record.embedding_status = "failed"
                    embedded_count -= 1
                    failed_count += 1
                    failed_chunk_ids.append(record.id)

            await db.commit()

            if progress_callback:
                await progress_callback(novel_id, embedded_count, total, "embedding")

        # 9. 更新最终状态
        if failed_count == 0:
            novel.status = "ready"
            final_status = "ready"
        else:
            # 不能把不可完整检索的小说报告为 ready；保留已成功的块，等待重建。
            novel.status = "indexing_failed"
            final_status = "partial"

        await db.commit()

        if progress_callback:
            await progress_callback(novel_id, total, total, final_status)

        # Phase 07: persist hierarchical build + active pointer (raw chunks remain)
        hierarchy_build_id: str | None = None
        try:
            hierarchy_build_id = await self._persist_hierarchy_build(
                db, novel_id=novel_id, chapter_payload=chapter_payload
            )
        except Exception as e:
            logger.warning(
                "层级 build 持久化失败 novel_%d: %s（raw 索引仍可用）",
                novel_id,
                e,
            )

        logger.info(
            "小说索引完成 novel_%d: total=%d, embedded=%d, failed=%d hierarchy=%s",
            novel_id,
            total,
            embedded_count,
            failed_count,
            hierarchy_build_id,
        )

        return {
            "novel_id": novel_id,
            "total_chunks": total,
            "embedded_chunks": embedded_count,
            "failed_chunks": failed_count,
            "failed_chunk_ids": failed_chunk_ids,
            "status": final_status,
            "hierarchy_build_id": hierarchy_build_id,
        }

    async def _persist_hierarchy_build(
        self,
        db: AsyncSession,
        *,
        novel_id: int,
        chapter_payload: list[dict[str, Any]],
    ) -> str:
        """Build chapter→scene→evidence tree, persist to PG, set active pointer."""
        from app.services.chunking.pg_store import create_and_persist_hierarchy_build

        chapters = [
            {
                "chapter_id": item["id"],
                "id": item["id"],
                "chapter_number": item["chapter_number"],
                "content": item["content"] or "",
            }
            for item in chapter_payload
        ]
        rec = await create_and_persist_hierarchy_build(
            db,
            novel_id=novel_id,
            chapters=chapters,
            promote_active=True,
            force_full=True,
        )
        await db.commit()
        return rec.build_id

    async def search_similar(
        self,
        db: AsyncSession,
        novel_id: int,
        query: str,
        top_k: int = 5,
        chunk_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        语义搜索入口。

        Args:
            db: 数据库会话
            novel_id: 小说 ID
            query: 查询文本
            top_k: 返回结果数量上限
            chunk_types: 可选的块类型过滤列表

        Returns:
            搜索结果列表，每项包含:
                - chunk_id: 文本块 ID
                - content: 文本内容
                - score: 相似度分数
                - chapter_id: 章节 ID
                - chunk_index: 块序号
                - chunk_type: 块类型
        """
        # 1. 生成查询向量
        query_embeddings = await ai_service.embedding(texts=[query])
        query_embedding = query_embeddings[0]

        # 2. 构建过滤条件
        filters = None
        if chunk_types and len(chunk_types) == 1:
            filters = {"chunk_type": chunk_types[0]}
        elif chunk_types and len(chunk_types) > 1:
            # ChromaDB 不直接支持 $in，通过多次搜索合并
            pass

        # 3. 向量检索
        raw_results = await self.vector_store.search(
            novel_id=novel_id,
            query_embedding=query_embedding,
            top_k=top_k,
            filters=filters,
        )

        # 4. 组装返回结果
        results = []
        for item in raw_results:
            metadata = item.get("metadata", {})
            chunk_id_str = item.get("chunk_id", "")
            # 从 "chunk_123" 中提取数字 ID
            chunk_id = (
                int(chunk_id_str.replace("chunk_", ""))
                if chunk_id_str.startswith("chunk_")
                else 0
            )

            # 如果需要多类型过滤，在返回阶段过滤
            if chunk_types and metadata.get("chunk_type") not in chunk_types:
                continue

            results.append(
                {
                    "chunk_id": chunk_id,
                    "content": item.get("content", ""),
                    "score": item.get("score", 0.0),
                    "chapter_id": metadata.get("chapter_id"),
                    "chunk_index": metadata.get("chunk_index"),
                    "chunk_type": metadata.get("chunk_type", "paragraph"),
                }
            )

        return results

    async def _batch_embed(
        self, texts: list[str], batch_size: int = 100
    ) -> list[list[float]]:
        """
        分批生成 embedding。

        Args:
            texts: 待向量化的文本列表
            batch_size: 每批大小（默认 100）

        Returns:
            向量列表，与输入 texts 一一对应

        Raises:
            Exception: API 调用失败时抛出
        """
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings = await self._with_retry(
                lambda: ai_service.embedding(texts=batch),
                operation_name=f"embedding batch {i // batch_size}",
            )
            if len(embeddings) != len(batch):
                raise IndexingError(
                    f"embedding 返回数量不匹配: expected={len(batch)}, got={len(embeddings)}"
                )
            all_embeddings.extend(embeddings)

        return all_embeddings


# 全局单例
indexing_service = IndexingService()
