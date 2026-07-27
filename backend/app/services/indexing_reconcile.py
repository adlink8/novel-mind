"""
raw TextChunk ↔ Chroma 一致性 reconcile（Phase 24-02）

对单本小说对比 PostgreSQL `text_chunks` 集与 Chroma collection
（`novel_{novel_id}`）的向量 id 集：

- missing : DB 有、向量无（含 embedding_status 为 pending/failed 的块——
  它们在 DB 中存在但从未成功写入向量，同样属于待修复缺口）；
- orphan  : 向量有、DB 无（残留旧向量，正是 24-01 之前删除窗口的产物）。

manifest 绑定：journal（`chunk_index_journal`）completed 记录携带
`manifest_checksum`（排序 `chunk_id:sha256(content)` 行集的 sha256，
见 `indexing_service.compute_chunk_manifest_checksum`）；reconcile 从 DB
复算同一公式并比对，证明当前 DB chunk 集与最近一次成功索引一致。

repair 模式：
- 补 embed missing（批量 embedding + add_chunks + embedding_status=embedded）；
- 删 orphan 向量；
- 修复全部成功且小说不再有 failed 块时，novel.status partial → ready；
- 每次 repair 写一条 kind='reconcile_repair' 的 journal 审计记录。

CLI 入口: backend/scripts/run_index_reconcile.py
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_index_journal import ChunkIndexJournal
from app.models.novel import Novel
from app.models.text_chunk import TextChunk
from app.services.indexing_service import compute_chunk_manifest_checksum
from app.services.vector_store import vector_store as default_vector_store

logger = logging.getLogger(__name__)

CHUNK_ID_PREFIX = "chunk_"
_PAGE_SIZE = 1000
_REPAIR_BATCH_SIZE = 64
_REPORT_ID_CAP = 500


class IndexReconcileError(Exception):
    """reconcile 操作异常基类"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IndexReconcileService:
    """DB text_chunks 与 Chroma collection 的一致性检查与修复。

    依赖注入友好：store 需提供 `get_named_collection` / `add_chunks`；
    embedder 需提供 `embedding(texts=[...])`（默认 ai_service，惰性导入）。
    """

    def __init__(self, store: Any = None, embedder: Any = None):
        self.vector_store = store or default_vector_store
        self._embedder = embedder

    def _embedding_service(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        from app.services.ai_service import ai_service

        return ai_service

    async def reconcile_novel(
        self,
        db: AsyncSession,
        novel_id: int,
        *,
        repair: bool = False,
    ) -> dict[str, Any]:
        """对比单本小说 DB chunk 集与 Chroma 向量集，可选修复。

        Returns:
            JSON 可序列化报告：missing/orphan 明细、embedding_status 计数、
            manifest 复算比对、consistent 结论、repair 结果（若执行）。
        """
        novel = await db.get(Novel, novel_id)
        if novel is None:
            raise IndexReconcileError(f"小说不存在: novel_id={novel_id}")

        rows = (
            await db.execute(
                select(
                    TextChunk.id,
                    TextChunk.content,
                    TextChunk.chapter_id,
                    TextChunk.chunk_index,
                    TextChunk.chunk_type,
                    TextChunk.word_count,
                    TextChunk.embedding_status,
                ).where(TextChunk.novel_id == novel_id)
            )
        ).all()

        db_ids = {row.id for row in rows}
        status_counts: dict[str, int] = {}
        for row in rows:
            key = row.embedding_status or "unknown"
            status_counts[key] = status_counts.get(key, 0) + 1

        raw_vector_ids = await self._collection_ids(novel_id)
        chroma_chunk_ids: set[int] = set()
        unparsed_vector_ids: list[str] = []
        for raw in raw_vector_ids:
            if isinstance(raw, str) and raw.startswith(CHUNK_ID_PREFIX):
                suffix = raw[len(CHUNK_ID_PREFIX) :]
                if suffix.isdigit():
                    chroma_chunk_ids.add(int(suffix))
                    continue
            unparsed_vector_ids.append(str(raw))

        missing_rows = [row for row in rows if row.id not in chroma_chunk_ids]
        orphan_chunk_ids = sorted(chroma_chunk_ids - db_ids)
        orphan_vector_ids = [
            f"{CHUNK_ID_PREFIX}{cid}" for cid in orphan_chunk_ids
        ] + unparsed_vector_ids

        manifest_recomputed = compute_chunk_manifest_checksum(
            [(row.id, row.content) for row in rows]
        )
        latest_completed = await self._latest_completed_journal(db, novel_id)
        manifest_journal = (
            latest_completed.manifest_checksum if latest_completed is not None else None
        )

        report: dict[str, Any] = {
            "novel_id": novel_id,
            "novel_status": novel.status,
            "db_chunks": len(rows),
            "chroma_vectors": len(raw_vector_ids),
            "embedding_status_counts": status_counts,
            "missing": {
                "count": len(missing_rows),
                "chunk_ids": [row.id for row in missing_rows][:_REPORT_ID_CAP],
            },
            "orphans": {
                "count": len(orphan_vector_ids),
                "vector_ids": orphan_vector_ids[:_REPORT_ID_CAP],
            },
            "manifest": {
                "recomputed": manifest_recomputed,
                "journal": manifest_journal,
                "journal_attempt_id": (
                    latest_completed.attempt_id
                    if latest_completed is not None
                    else None
                ),
                "match": (
                    manifest_recomputed == manifest_journal
                    if manifest_journal is not None
                    else None
                ),
            },
            "consistent": not missing_rows and not orphan_vector_ids,
            "repair": None,
        }

        if repair and (missing_rows or orphan_vector_ids):
            report["repair"] = await self._repair(
                db,
                novel=novel,
                missing_rows=missing_rows,
                orphan_vector_ids=orphan_vector_ids,
                manifest_checksum=manifest_recomputed,
            )
            report["novel_status"] = novel.status

        return report

    async def _repair(
        self,
        db: AsyncSession,
        *,
        novel: Novel,
        missing_rows: list[Any],
        orphan_vector_ids: list[str],
        manifest_checksum: str,
    ) -> dict[str, Any]:
        """补 embed missing、删 orphan，并写 reconcile_repair journal 审计。"""
        novel_id = novel.id
        journal = ChunkIndexJournal(
            novel_id=novel_id,
            attempt_id=uuid.uuid4().hex,
            kind="reconcile_repair",
            phase="started",
            collection_name=f"novel_{novel_id}",
            started_at=_utcnow(),
        )
        db.add(journal)
        await db.commit()

        embedded = 0
        failed = 0
        deleted_orphans = 0
        errors: list[str] = []

        try:
            if orphan_vector_ids:
                await self._delete_vectors(novel_id, orphan_vector_ids)
                deleted_orphans = len(orphan_vector_ids)

            embedder = self._embedding_service()
            for i in range(0, len(missing_rows), _REPAIR_BATCH_SIZE):
                batch = missing_rows[i : i + _REPAIR_BATCH_SIZE]
                try:
                    embeddings = await embedder.embedding(
                        texts=[row.content for row in batch]
                    )
                    await self.vector_store.add_chunks(
                        novel_id=novel_id,
                        chunks=[
                            {
                                "id": row.id,
                                "content": row.content,
                                "embedding": embeddings[j],
                                "metadata": {
                                    "chapter_id": row.chapter_id,
                                    "chunk_index": row.chunk_index,
                                    "chunk_type": row.chunk_type,
                                    "word_count": row.word_count,
                                },
                            }
                            for j, row in enumerate(batch)
                        ],
                    )
                    await db.execute(
                        update(TextChunk)
                        .where(TextChunk.id.in_([row.id for row in batch]))
                        .values(embedding_status="embedded")
                    )
                    embedded += len(batch)
                except Exception as e:
                    logger.error(
                        "reconcile 补 embed 失败 novel_%d batch_%d: %s",
                        novel_id,
                        i // _REPAIR_BATCH_SIZE,
                        e,
                    )
                    failed += len(batch)
                    errors.append(str(e)[:200])

            journal.total_chunks = len(missing_rows)
            journal.embedded_chunks = embedded
            journal.failed_chunks = failed
            journal.finished_at = _utcnow()
            if failed == 0:
                journal.phase = "completed"
                journal.manifest_checksum = manifest_checksum
                # 修复干净且不再有 failed 块 → partial 小说恢复 ready
                if novel.status == "partial":
                    still_failed = (
                        await db.execute(
                            select(func.count()).where(
                                TextChunk.novel_id == novel_id,
                                TextChunk.embedding_status == "failed",
                            )
                        )
                    ).scalar()
                    if not still_failed:
                        novel.status = "ready"
            else:
                journal.phase = "failed"
                journal.error_summary = "; ".join(errors)[:2000]
            await db.commit()
        except Exception as e:
            journal.phase = "failed"
            journal.error_summary = str(e)[:2000]
            journal.finished_at = _utcnow()
            await db.commit()
            raise

        return {
            "attempt_id": journal.attempt_id,
            "embedded_missing": embedded,
            "failed": failed,
            "deleted_orphans": deleted_orphans,
            "errors": errors[:5],
            "novel_status": novel.status,
        }

    async def _latest_completed_journal(
        self, db: AsyncSession, novel_id: int
    ) -> ChunkIndexJournal | None:
        result = await db.execute(
            select(ChunkIndexJournal)
            .where(
                ChunkIndexJournal.novel_id == novel_id,
                ChunkIndexJournal.kind == "index",
                ChunkIndexJournal.phase == "completed",
            )
            .order_by(ChunkIndexJournal.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _collection_ids(self, novel_id: int) -> list[str]:
        """分页拉取 Chroma collection 全部向量 id（collection 不存在视为空）。"""

        def _fetch() -> list[str]:
            try:
                collection = self.vector_store.get_named_collection(f"novel_{novel_id}")
            except Exception as e:
                logger.info("novel_%d 向量集合不可读（视为空）: %s", novel_id, e)
                return []
            ids: list[str] = []
            offset = 0
            while True:
                result = collection.get(limit=_PAGE_SIZE, offset=offset, include=[])
                page = (result or {}).get("ids") or []
                ids.extend(page)
                if len(page) < _PAGE_SIZE:
                    break
                offset += len(page)
            return ids

        return await asyncio.to_thread(_fetch)

    async def _delete_vectors(self, novel_id: int, vector_ids: list[str]) -> None:
        def _delete() -> None:
            collection = self.vector_store.get_named_collection(f"novel_{novel_id}")
            collection.delete(ids=list(vector_ids))

        await asyncio.to_thread(_delete)


# 全局单例（生产依赖默认 vector_store / ai_service）
index_reconcile_service = IndexReconcileService()
