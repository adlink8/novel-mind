"""
Embedding 索引管线服务

将小说文本分块后生成 embedding 向量并存储到 ChromaDB，
是 RAG 检索系统的核心编排层。

完整流程（Phase 24-01：journal/幂等/fail-closed）:
  1. 从数据库读取小说和章节，计算 source_signature（幂等键）
  2. 基于 chunk_index_journal 判定：进行中 → 拒绝并发；
     同内容已 completed 且零失败 → 跳过（force=True 显式重建）
  3. 创建 journal（phase=started），更新 Novel.status = "chunking"
  4. 调用 chunking_service.chunk_novel 分块
  5. phase=deleting_old：**先删 Chroma collection，再删 text_chunks 行**；
     Chroma 删除失败 fail-closed（journal 记 failed，抛错，不再 warning 继续）
  6. 将分块写入 text_chunks 表（phase=chunks_persisted）
  7. 更新 Novel.status = "embedding"（journal phase=embedding）
  8. 批量调用 ai_service.embedding 生成向量 + vector_store.add_chunks 写入
     （journal 计数随批次提交推进）
  9. failed_count == 0 → Novel.status = "ready"，journal phase=completed
     并写入 manifest_checksum（Phase 24-02 reconcile 绑定）；
     failed_count > 0 → Novel.status = "partial"（fail-closed，检索侧可感知），
     journal phase=failed + error 摘要
  10. 返回统计信息（含 attempt_id）

使用方式:
  from app.services.indexing_service import indexing_service
  result = await indexing_service.index_novel(db, novel_id=1)
  results = await indexing_service.search_similar(db, novel_id=1, query="主角的性格")
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chunk_index_journal import (
    INDEX_JOURNAL_IN_PROGRESS_PHASES,
    ChunkIndexJournal,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.services.ai_service import ai_service
from app.services.chunking_service import ChunkingService
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# 进度回调类型: (novel_id, current, total, status)
ProgressCallback = Callable[[int, int, str], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_source_signature(chapter_payload: list[dict[str, Any]]) -> str:
    """章节内容幂等键：排序 `chapter_id:chapter_number:sha256(content)` 行的 sha256。"""
    lines = sorted(
        f"{item['id']}:{item['chapter_number']}:{_sha256_text(item['content'] or '')}"
        for item in chapter_payload
    )
    return _sha256_text("\n".join(lines))


def compute_chunk_manifest_checksum(pairs: list[tuple[Any, str]]) -> str:
    """chunk 集 manifest：排序 `chunk_id:sha256(content)` 行的 sha256。

    reconcile（Phase 24-02）从 DB text_chunks 复算同一公式与 journal 比对。
    """
    lines = sorted(f"{chunk_id}:{_sha256_text(content)}" for chunk_id, content in pairs)
    return _sha256_text("\n".join(lines))


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

    async def index_novel(
        self,
        db: AsyncSession,
        novel_id: int,
        progress_callback: ProgressCallback | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        完整的小说索引流程（journal 贯穿、幂等、fail-closed）。

        Args:
            db: 数据库会话
            novel_id: 小说 ID
            progress_callback: 进度回调函数，签名为 (novel_id, current, total, status)
            force: True 时跳过幂等检查并接管进行中的旧尝试（显式重建语义）

        Returns:
            统计信息字典，包含:
                - novel_id: 小说 ID
                - total_chunks: 总块数
                - embedded_chunks: 成功向量化的块数
                - failed_chunks: 失败的块数
                - status: 最终状态 ("ready" 或 "partial")
                - attempt_id: 本次索引尝试的 journal 标识
                - skipped: 幂等命中（同内容已完成）时为 True

        Raises:
            IndexingError: 流程级错误（小说不存在、章节为空、并发索引、
                旧向量清理失败等 fail-closed 场景）
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

        # 2. 幂等键 + 并发判定（基于 journal）
        source_signature = compute_source_signature(chapter_payload)
        latest = await self._latest_journal(db, novel_id)
        if latest is not None:
            in_progress = (
                latest.finished_at is None
                and latest.phase in INDEX_JOURNAL_IN_PROGRESS_PHASES
            )
            if in_progress:
                if not force:
                    raise IndexingError(
                        f"索引已在进行中 novel_id={novel_id} "
                        f"attempt={latest.attempt_id} phase={latest.phase}；"
                        "如需强制重建请使用 force=True"
                    )
                latest.phase = "failed"
                latest.error_summary = "superseded by new forced attempt"
                latest.finished_at = _utcnow()
                await db.commit()
            elif (
                not force
                and latest.phase == "completed"
                and latest.source_signature == source_signature
                and (latest.failed_chunks or 0) == 0
            ):
                logger.info(
                    "索引幂等命中 novel_%d attempt=%s（同内容已完成，跳过）",
                    novel_id,
                    latest.attempt_id,
                )
                return {
                    "novel_id": novel_id,
                    "total_chunks": latest.total_chunks,
                    "embedded_chunks": latest.embedded_chunks,
                    "failed_chunks": latest.failed_chunks,
                    "status": "ready",
                    "attempt_id": latest.attempt_id,
                    "skipped": True,
                }

        # 3. 创建 journal + 状态 chunking
        journal = ChunkIndexJournal(
            novel_id=novel_id,
            attempt_id=uuid.uuid4().hex,
            kind="index",
            phase="started",
            collection_name=f"novel_{novel_id}",
            source_signature=source_signature,
            started_at=_utcnow(),
        )
        db.add(journal)
        novel.status = "chunking"
        await db.commit()

        try:
            return await self._run_index(
                db,
                novel=novel,
                journal=journal,
                chapter_payload=chapter_payload,
                progress_callback=progress_callback,
            )
        except Exception as e:
            # fail-closed：任何流程级失败都落 journal，novel 不得停留在中间态
            try:
                journal.phase = "failed"
                journal.error_summary = str(e)[:2000]
                journal.finished_at = _utcnow()
                novel.status = "failed"
                await db.commit()
            except Exception:
                logger.exception(
                    "索引失败后写 journal 失败 novel_%d attempt=%s",
                    novel_id,
                    journal.attempt_id,
                )
            raise

    async def _latest_journal(
        self, db: AsyncSession, novel_id: int
    ) -> ChunkIndexJournal | None:
        """取该小说最近一次 kind='index' 的 journal 记录。"""
        result = await db.execute(
            select(ChunkIndexJournal)
            .where(
                ChunkIndexJournal.novel_id == novel_id,
                ChunkIndexJournal.kind == "index",
            )
            .order_by(ChunkIndexJournal.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _run_index(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        journal: ChunkIndexJournal,
        chapter_payload: list[dict[str, Any]],
        progress_callback: ProgressCallback | None,
    ) -> dict[str, Any]:
        """journal 已创建后的主索引流程（异常由 index_novel 统一 fail-closed）。"""
        novel_id = novel.id

        if progress_callback:
            await progress_callback(novel_id, 0, 0, "chunking")

        # 4. 分块
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
            journal.phase = "completed"
            journal.manifest_checksum = compute_chunk_manifest_checksum([])
            journal.finished_at = _utcnow()
            await db.commit()
            return {
                "novel_id": novel_id,
                "total_chunks": 0,
                "embedded_chunks": 0,
                "failed_chunks": 0,
                "status": "ready",
                "attempt_id": journal.attempt_id,
            }

        # 5. 清理旧向量与旧分块（维度/模型切换后必须重建，避免 768/512 混用）。
        #    顺序：先删 Chroma、后删 DB 行——失败时 fail-closed 抛错，
        #    消除"DB 已删、旧向量残留"窗口（旧实现先删 DB、Chroma 失败仅 warning）。
        journal.phase = "deleting_old"
        await db.commit()

        try:
            await self.vector_store.delete_novel_chunks(novel_id)
        except Exception as e:
            raise IndexingError(
                f"清理旧 Chroma collection 失败 novel_{novel_id}: {e}"
            ) from e

        from sqlalchemy import delete as sa_delete

        await db.execute(sa_delete(TextChunk).where(TextChunk.novel_id == novel_id))
        await db.commit()

        # 6. 将分块写入 text_chunks 表
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

        journal.phase = "chunks_persisted"
        journal.total_chunks = len(chunk_records)
        await db.commit()

        # 刷新以获取自动生成的 id
        for record in chunk_records:
            await db.refresh(record)

        # 7. 更新状态为 embedding
        novel.status = "embedding"
        journal.phase = "embedding"
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
                journal.embedded_chunks = embedded_count
                journal.failed_chunks = failed_count
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
                await self.vector_store.add_chunks(
                    novel_id=novel_id, chunks=chunks_for_store
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

            journal.embedded_chunks = embedded_count
            journal.failed_chunks = failed_count
            await db.commit()

            if progress_callback:
                await progress_callback(novel_id, embedded_count, total, "embedding")

        # 9. 更新最终状态（fail-closed：部分失败不得声明 ready）
        journal.embedded_chunks = embedded_count
        journal.failed_chunks = failed_count
        journal.finished_at = _utcnow()
        if failed_count == 0:
            novel.status = "ready"
            final_status = "ready"
            journal.phase = "completed"
            # Phase 24-02 manifest 绑定：chunk 集 checksum，reconcile 可从 DB 复算
            journal.manifest_checksum = compute_chunk_manifest_checksum(
                [(record.id, record.content) for record in chunk_records]
            )
        else:
            novel.status = "partial"
            final_status = "partial"
            journal.phase = "failed"
            journal.error_summary = (
                f"partial: {failed_count}/{total} chunks failed embedding"
            )

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
            "小说索引完成 novel_%d: total=%d, embedded=%d, failed=%d "
            "status=%s attempt=%s hierarchy=%s",
            novel_id,
            total,
            embedded_count,
            failed_count,
            final_status,
            journal.attempt_id,
            hierarchy_build_id,
        )

        return {
            "novel_id": novel_id,
            "total_chunks": total,
            "embedded_chunks": embedded_count,
            "failed_chunks": failed_count,
            "failed_chunk_ids": failed_chunk_ids,
            "status": final_status,
            "attempt_id": journal.attempt_id,
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
            embeddings = await ai_service.embedding(texts=batch)
            all_embeddings.extend(embeddings)

        return all_embeddings


# 全局单例
indexing_service = IndexingService()
