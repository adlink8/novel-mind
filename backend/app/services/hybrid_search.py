"""
混合搜索服务：语义向量 + PostgreSQL BM25 关键词加权融合

提供跨全部小说和单本小说内的混合搜索能力。
混合策略：向量搜索结果与 BM25 全文搜索结果按 0.5:0.5 权重融合排序。

BM25 实现使用 PostgreSQL tsvector + tsquery + ts_rank_cd，
配合 pg_catalog.simple 分词器（按字切分，无需安装中文分词扩展）。

使用方式:
    from app.services.hybrid_search import hybrid_search_service
    results = await hybrid_search_service.search_novel(db, novel_id=1, query="龙影")
    results = await hybrid_search_service.search_global(db, query="龙影", owner_id=1)
"""

import logging
from typing import Any

from sqlalchemy import bindparam, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.services.vector_store import vector_store
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)


class HybridSearchError(Exception):
    """混合搜索操作异常基类"""


class HybridSearchService:
    """混合搜索：语义向量 + PostgreSQL BM25 关键词加权融合"""

    def __init__(self):
        self.vector_store = vector_store

    async def search_global(
        self,
        db: AsyncSession,
        query: str,
        top_k: int = 10,
        owner_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """跨所有小说全局搜索。

        执行 BM25 搜索（不限 novel_id），对每个有结果的 novel_id
        执行向量搜索，最后 hybrid_rerank 融合。

        Args:
            db: 数据库会话
            query: 搜索查询文本
            top_k: 返回结果数量上限
            owner_id: 可选的用户 ID，限定搜索范围

        Returns:
            搜索结果列表，每项包含 novel_id, novel_title, chapter_id,
            chapter_title, chunk_id, chunk_index, content_snippet, score 等
        """
        # 1. BM25 全文搜索（跨全部小说，限制 2x top_k 供融合用）
        bm25_results = await self._bm25_search(
            db, query=query, novel_id=None, limit=top_k * 2, owner_id=owner_id
        )

        if not bm25_results:
            return []

        # 2. 收集涉及的小说 ID，对每本小说执行向量搜索
        novel_ids = list({r["novel_id"] for r in bm25_results})
        all_vector_results: list[dict[str, Any]] = []

        for nid in novel_ids:
            try:
                vec_results = await self._vector_search(
                    query=query, novel_id=nid, top_k=top_k
                )
                # 向量结果需要补上 novel_id（ChromaDB 返回的不一定包含）
                for item in vec_results:
                    item["novel_id"] = nid
                all_vector_results.extend(vec_results)
            except Exception as e:
                logger.warning("向量搜索失败 novel_%d: %s", nid, e)

        # 3. 混合融合排序
        merged = await self._hybrid_rerank(bm25_results, all_vector_results, top_k)
        # 4. hierarchy enrich per novel (raw fallback if missing)
        enriched: list[dict[str, Any]] = []
        for item in merged:
            nid = item.get("novel_id")
            if isinstance(nid, int):
                one = await self._enrich_with_hierarchy(db, nid, [item])
                enriched.extend(one)
            else:
                item = dict(item)
                item["hierarchy_mode"] = "raw_fallback"
                enriched.append(item)
        return enriched

    async def search_novel(
        self,
        db: AsyncSession,
        novel_id: int,
        query: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """小说内混合搜索。

        Args:
            db: 数据库会话
            novel_id: 小说 ID
            query: 搜索查询文本
            top_k: 返回结果数量上限

        Returns:
            搜索结果列表
        """
        # 1. BM25 搜索（限制 2x top_k 供融合用）
        bm25_results = await self._bm25_search(
            db, query=query, novel_id=novel_id, limit=top_k * 2
        )

        # 2. 向量搜索
        vector_results: list[dict[str, Any]] = []
        try:
            vector_results = await self._vector_search(
                query=query, novel_id=novel_id, top_k=top_k
            )
            for item in vector_results:
                item["novel_id"] = novel_id
        except Exception as e:
            logger.warning("向量搜索失败 novel_%d: %s", novel_id, e)

        # 3. 融合排序
        merged = await self._hybrid_rerank(bm25_results, vector_results, top_k)
        # 4. Phase 07 hierarchy: evidence hit → scene expand (raw fallback)
        return await self._enrich_with_hierarchy(db, novel_id, merged)

    async def _enrich_with_hierarchy(
        self,
        db: AsyncSession,
        novel_id: int,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Attach scene context when active hierarchy exists; never fail search."""
        if not results:
            return results
        try:
            from app.services.chunking.pg_store import expand_search_result_with_hierarchy

            enriched: list[dict[str, Any]] = []
            for item in results:
                try:
                    enriched.append(
                        await expand_search_result_with_hierarchy(
                            db, novel_id=novel_id, result=item
                        )
                    )
                except Exception as e:
                    logger.debug("hierarchy enrich skip chunk=%s: %s", item.get("chunk_id"), e)
                    item = dict(item)
                    item["hierarchy_mode"] = "raw_fallback"
                    enriched.append(item)
            return enriched
        except Exception as e:
            logger.warning("hierarchy enrich unavailable novel_%d: %s", novel_id, e)
            return results

    async def _bm25_search(
        self,
        db: AsyncSession,
        query: str,
        novel_id: int | None = None,
        limit: int = 20,
        owner_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """PostgreSQL 全文搜索（tsvector + tsquery + ts_rank_cd）。

        使用 pg_catalog.simple 分词器，按字切分中文文本。
        返回带高亮片段的搜索结果。

        Args:
            db: 数据库会话
            query: 搜索查询文本
            novel_id: 可选的过滤小说 ID
            limit: 返回结果数量上限
            owner_id: 可选的用户 ID 过滤

        Returns:
            搜索结果列表，每项包含 novel_id, chunk_id, content_snippet (高亮),
            score, chapter_id, chapter_title, novel_title, chunk_index
        """
        params: dict[str, Any] = {"query": query, "limit": limit}
        tsquery = func.plainto_tsquery("simple", bindparam("query"))
        rank = func.ts_rank_cd(TextChunk.search_vector, tsquery)
        headline = func.ts_headline(
            "simple",
            TextChunk.content,
            tsquery,
            "MaxWords=50, MinWords=20, StartSel=<mark>, StopSel=</mark>",
        )
        query_sql = (
            select(
                TextChunk.id.label("chunk_id"),
                TextChunk.content,
                TextChunk.chapter_id,
                TextChunk.chunk_index,
                TextChunk.novel_id,
                rank.label("rank"),
                headline.label("headline"),
                func.coalesce(Chapter.title, "").label("chapter_title"),
                func.coalesce(Novel.title, "").label("novel_title"),
            )
            .join(Novel, Novel.id == TextChunk.novel_id)
            .outerjoin(Chapter, Chapter.id == TextChunk.chapter_id)
            .where(TextChunk.search_vector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(bindparam("limit"))
        )

        if novel_id is not None:
            query_sql = query_sql.where(TextChunk.novel_id == bindparam("novel_id"))
            params["novel_id"] = novel_id

        if owner_id is not None:
            query_sql = query_sql.where(Novel.owner_id == bindparam("owner_id"))
            params["owner_id"] = owner_id

        try:
            result = await db.execute(query_sql, params)
            rows = result.fetchall()
        except Exception as e:
            logger.error("BM25 搜索失败: %s", e)
            return []

        # Pull hierarchy columns when present (SQLite tests may lack them pre-migration)
        out: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {
                "novel_id": row.novel_id,
                "novel_title": row.novel_title or "",
                "chapter_id": row.chapter_id,
                "chapter_title": row.chapter_title or "",
                "chunk_id": row.chunk_id,
                "chunk_index": row.chunk_index,
                "content_snippet": row.headline or (row.content[:100] if row.content else ""),
                "score": float(row.rank),
            }
            out.append(item)
        # Best-effort attach hierarchy ids from ORM for enrichment
        try:
            chunk_ids = [i["chunk_id"] for i in out if i.get("chunk_id")]
            if chunk_ids:
                rows2 = (
                    await db.execute(
                        select(
                            TextChunk.id,
                            TextChunk.hierarchy_node_id,
                            TextChunk.hierarchy_build_id,
                            TextChunk.hierarchy_level,
                            TextChunk.hierarchy_parent_id,
                            TextChunk.source_start,
                            TextChunk.source_end,
                        ).where(TextChunk.id.in_(chunk_ids))
                    )
                ).all()
                by_id = {r.id: r for r in rows2}
                for item in out:
                    r = by_id.get(item["chunk_id"])
                    if r and r.hierarchy_node_id:
                        item["hierarchy_node_id"] = r.hierarchy_node_id
                        item["hierarchy_build_id"] = r.hierarchy_build_id
                        item["hierarchy_level"] = r.hierarchy_level
                        item["hierarchy_parent_id"] = r.hierarchy_parent_id
                        item["source_start"] = r.source_start
                        item["source_end"] = r.source_end
        except Exception as e:
            logger.debug("hierarchy column attach skipped: %s", e)
        return out

    async def _vector_search(
        self,
        query: str,
        novel_id: int,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """ChromaDB 语义搜索。

        生成查询 embedding 后在对应小说集合中检索最相似文本块。

        Args:
            query: 搜索查询文本
            novel_id: 小说 ID
            top_k: 返回结果数量上限

        Returns:
            搜索结果列表，每项包含 chunk_id, content, score, metadata
        """
        try:
            # 生成查询向量
            query_embeddings = await ai_service.embedding(texts=[query])
            query_embedding = query_embeddings[0]

            # ChromaDB 向量检索
            raw_results = await self.vector_store.search(
                novel_id=novel_id,
                query_embedding=query_embedding,
                top_k=top_k,
            )

            results = []
            for item in raw_results:
                metadata = item.get("metadata", {})
                chunk_id_str = item.get("chunk_id", "")
                chunk_id = (
                    int(chunk_id_str.replace("chunk_", ""))
                    if chunk_id_str.startswith("chunk_")
                    else 0
                )
                results.append({
                    "chunk_id": chunk_id,
                    "content": item.get("content", ""),
                    "score": item.get("score", 0.0),
                    "chapter_id": metadata.get("chapter_id"),
                    "chunk_index": metadata.get("chunk_index"),
                })

            return results
        except Exception as e:
            logger.warning("向量搜索失败 novel_%d: %s", novel_id, e)
            return []

    async def _hybrid_rerank(
        self,
        bm25_results: list[dict[str, Any]],
        vector_results: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """融合排序：50% 向量分数 + 50% BM25 分数。

        策略:
        1. BM25 分数归一化（除以最大值）
        2. 向量分数（余弦相似度）已在 0-1 范围
        3. 加权: weight_vector=0.5, weight_bm25=0.5
        4. 同一 chunk_id 的分数合并（去重）
        5. 按最终分数降序排列

        Args:
            bm25_results: BM25 搜索结果
            vector_results: 向量搜索结果
            top_k: 最终返回结果数量

        Returns:
            融合排序后的结果列表
        """
        weight_vector = 0.5
        weight_bm25 = 0.5

        # 键: chunk_id, 值: 累积信息
        merged: dict[int, dict[str, Any]] = {}

        # 处理 BM25 结果
        max_bm25 = max((r["score"] for r in bm25_results), default=1.0)
        for r in bm25_results:
            chunk_id = r["chunk_id"]
            norm_score = r["score"] / max_bm25 if max_bm25 > 0 else 0.0

            if chunk_id not in merged:
                merged[chunk_id] = {
                    "novel_id": r.get("novel_id"),
                    "novel_title": r.get("novel_title", ""),
                    "chapter_id": r.get("chapter_id"),
                    "chapter_title": r.get("chapter_title", ""),
                    "chunk_id": chunk_id,
                    "chunk_index": r.get("chunk_index", 0),
                    "content_snippet": r.get("content_snippet", r.get("content", "")),
                    "bm25_score": norm_score,
                    "vector_score": 0.0,
                    "score": 0.0,
                }
            else:
                merged[chunk_id]["bm25_score"] = max(
                    merged[chunk_id]["bm25_score"], norm_score
                )
                # 合并时保留更好的 snippet
                if r.get("content_snippet") and len(r.get("content_snippet", "")) > len(
                    merged[chunk_id].get("content_snippet", "")
                ):
                    merged[chunk_id]["content_snippet"] = r["content_snippet"]

        # 处理向量结果
        max_vector = max((r["score"] for r in vector_results), default=1.0)
        for r in vector_results:
            chunk_id = r.get("chunk_id")
            if chunk_id is None or chunk_id == 0:
                continue
            norm_score = r["score"] / max_vector if max_vector > 0 else 0.0

            if chunk_id not in merged:
                merged[chunk_id] = {
                    "novel_id": r.get("novel_id"),
                    "novel_title": r.get("novel_title", ""),
                    "chapter_id": r.get("chapter_id"),
                    "chapter_title": "",
                    "chunk_id": chunk_id,
                    "chunk_index": r.get("chunk_index", 0),
                    "content_snippet": r.get("content", "")[:200],
                    "bm25_score": 0.0,
                    "vector_score": norm_score,
                    "score": 0.0,
                }
            else:
                merged[chunk_id]["vector_score"] = max(
                    merged[chunk_id]["vector_score"], norm_score
                )

        # 计算最终分数
        for info in merged.values():
            info["score"] = round(
                weight_bm25 * info["bm25_score"] + weight_vector * info["vector_score"],
                4,
            )

        # 按最终分数降序排列，取 top_k
        sorted_items = sorted(
            merged.values(), key=lambda x: x["score"], reverse=True
        )
        return sorted_items[:top_k]


# 全局单例
hybrid_search_service = HybridSearchService()
