"""
评测服务 — RAG 检索质量自动化评测引擎 + 06-04 质量路径适配

支持策略:
  - bm25            : PostgreSQL 全文搜索（无需 Ollama）
  - baseline_vector : 纯向量语义搜索 (ChromaDB, 需 Ollama)
  - hybrid_search   : BM25 + 向量加权融合 (需 Ollama)

指标 (legacy retrieval):
  - recall@k, precision@k, MRR, NDCG@k

Phase 06-04:
  - Legacy API keeps working with deprecation metadata
  - Quality path must NEVER swallow exceptions into 0 scores
  - gold_chunks-only cases are not quality-comparable
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import EvalDataset, EvalRun, EvalResult
from app.services.canon_fork.contamination import (
    ORIGINAL_CANON,
    evaluation_corpus_guard,
)

logger = logging.getLogger(__name__)

DEPRECATION_META = {
    "deprecated": True,
    "legacy_eval_api": True,
    "replacement": {
        "create": "POST /api/eval/quality/runs",
        "status": "GET /api/eval/quality/runs/{job_id}",
        "report": "GET /api/eval/quality/runs/{job_id}",
        "resume": "POST /api/eval/quality/runs/{job_id}/resume",
        "cancel": "POST /api/eval/quality/runs/{job_id}/cancel",
    },
    "migration": (
        "Migrate gold_chunks DB IDs to content-hash EvidenceRef fixtures "
        "(scripts/migrate_legacy_eval.py). Qualification rejects DB-id-only truth."
    ),
    "quality_comparable_default": False,
}


class EvalServiceError(Exception):
    """评测操作异常基类"""


class EvalService:
    """RAG 评测引擎（legacy retrieval + quality adapter helpers）"""

    def __init__(self):
        self.supported_strategies = {
            "bm25",
            "baseline_vector",
            "hybrid_search",
        }
        self._ai_service = None
        self._vector_store = None
        self._hybrid_search = None

    @property
    def ai_service(self):
        if self._ai_service is None:
            from app.services.ai_service import ai_service

            self._ai_service = ai_service
        return self._ai_service

    @property
    def vector_store(self):
        if self._vector_store is None:
            from app.services.vector_store import vector_store as vs

            self._vector_store = vs
        return self._vector_store

    @property
    def hybrid_search(self):
        if self._hybrid_search is None:
            from app.services.hybrid_search import hybrid_search_service

            self._hybrid_search = hybrid_search_service
        return self._hybrid_search

    async def run_eval(
        self,
        db: AsyncSession,
        run_name: str,
        strategy: str,
        novel_id: int,
        dataset_ids: list[int],
        top_k: int = 5,
        *,
        quality_mode: bool = False,
        raise_on_item_error: bool | None = None,
        space: str = ORIGINAL_CANON,
    ) -> dict[str, Any]:
        """
        执行一次完整评测运行。

        quality_mode / raise_on_item_error:
          When True, per-item exceptions are NOT converted into zero scores
          (06-04 D-07). Legacy default still records error_case with zeros for
          retrieval-only compatibility, but response marks quality_comparable=false.
        """
        # Shared derivative-write guard: the evaluation corpus may only be fed
        # by Original Canon content (REQ-CRE-02 / D-35-02).
        evaluation_corpus_guard.assert_write_allowed(
            space=space, novel_id=novel_id
        )
        if strategy not in self.supported_strategies:
            raise EvalServiceError(
                f"不支持的策略: {strategy}，有效值: {self.supported_strategies}"
            )

        strict = quality_mode if raise_on_item_error is None else raise_on_item_error

        result = await db.execute(
            select(EvalDataset).where(
                EvalDataset.id.in_(dataset_ids),
                EvalDataset.novel_id == novel_id,
            )
        )
        datasets = result.scalars().all()

        if not datasets:
            raise EvalServiceError("没有找到匹配的测试题")

        # Legacy retrieval is never quality-comparable without signed fixtures
        run = EvalRun(
            run_name=run_name,
            strategy=strategy,
            novel_id=novel_id,
            total_questions=len(datasets),
            config_snapshot={
                "top_k": top_k,
                "strategy": strategy,
                "quality_mode": quality_mode,
                "quality_comparable": False,
                "deprecation": DEPRECATION_META,
            },
        )
        db.add(run)
        await db.flush()

        t_start = time.time()
        all_metrics: list[dict[str, Any]] = []
        item_errors: list[dict[str, Any]] = []

        for idx, ds in enumerate(datasets):
            try:
                if strategy == "hybrid_search":
                    search_results = await self.hybrid_search.search_novel(
                        db=db,
                        novel_id=novel_id,
                        query=ds.question,
                        top_k=top_k,
                    )
                elif strategy == "baseline_vector":
                    search_results = await self._baseline_vector_search(
                        query=ds.question,
                        novel_id=novel_id,
                        top_k=top_k,
                    )
                elif strategy == "bm25":
                    search_results = await self._bm25_search(
                        db=db,
                        query=ds.question,
                        novel_id=novel_id,
                        top_k=top_k,
                    )
                else:
                    search_results = []

                recalled_chunk_ids = [r["chunk_id"] for r in search_results]
                recalled_scores = [
                    r.get("score", r.get("vector_score", 0.0)) for r in search_results
                ]

                metrics = self._compute_metrics(
                    gold_chunks=ds.gold_chunks
                    if isinstance(ds.gold_chunks, list)
                    else [],
                    recalled_chunks=recalled_chunk_ids,
                    top_k=top_k,
                    recalled_scores=recalled_scores,
                )

                er = EvalResult(
                    run_id=run.id,
                    dataset_id=ds.id,
                    recalled_chunks=recalled_chunk_ids,
                    score=metrics.get("recall_at_k", 0.0),
                    metrics=metrics,
                    is_error_case=(metrics.get("recall_at_k", 0.0) == 0.0),
                )
                db.add(er)
                all_metrics.append(metrics)

            except Exception as exc:
                logger.warning("评测第 %d 题失败: %s", idx + 1, exc)
                if strict:
                    # Quality path: do not convert to zero scores
                    await db.rollback()
                    raise EvalServiceError(
                        f"评测第 {idx + 1} 题失败（quality path 不吞异常）: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc

                er = EvalResult(
                    run_id=run.id,
                    dataset_id=ds.id,
                    recalled_chunks=[],
                    score=0.0,
                    metrics={"error": str(exc), "quality_comparable": False},
                    is_error_case=True,
                )
                db.add(er)
                # Legacy aggregation still uses zeros for retrieval metrics only
                all_metrics.append(
                    {
                        "recall_at_k": 0.0,
                        "precision_at_k": 0.0,
                        "mrr": 0.0,
                        "ndcg_at_k": 0.0,
                    }
                )
                item_errors.append(
                    {"dataset_id": ds.id, "error": f"{type(exc).__name__}: {exc}"}
                )

        elapsed_ms = (time.time() - t_start) * 1000

        n = len(all_metrics)
        run.recall_at_k = (
            sum(m["recall_at_k"] for m in all_metrics) / n if n > 0 else 0.0
        )
        run.precision_at_k = (
            sum(m.get("precision_at_k", 0.0) for m in all_metrics) / n if n > 0 else 0.0
        )
        run.mrr = sum(m.get("mrr", 0.0) for m in all_metrics) / n if n > 0 else 0.0
        run.ndcg_at_k = (
            sum(m.get("ndcg_at_k", 0.0) for m in all_metrics) / n if n > 0 else 0.0
        )
        run.latency_ms = elapsed_ms
        run.total_questions = n

        await db.commit()

        return self._legacy_run_payload(
            run_id=run.id,
            total_questions=n,
            recall_at_k=run.recall_at_k,
            precision_at_k=run.precision_at_k or 0.0,
            mrr=run.mrr or 0.0,
            ndcg_at_k=run.ndcg_at_k or 0.0,
            latency_ms=elapsed_ms,
            job_id=None,
            status="completed",
            item_errors=item_errors,
        )

    def _legacy_run_payload(
        self,
        *,
        run_id: int,
        total_questions: int,
        recall_at_k: float,
        precision_at_k: float,
        mrr: float,
        ndcg_at_k: float,
        latency_ms: float,
        job_id: str | None,
        status: str,
        item_errors: list[dict[str, Any]] | None = None,
        quality_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Preserve legacy fields; append job/status/quality/deprecation."""
        quality_comparable = False
        metrics_block = None
        if quality_report is not None:
            quality_comparable = bool(quality_report.get("quality_comparable"))
            metrics_block = (
                quality_report.get("metrics") if quality_comparable else None
            )
            status = quality_report.get("status", status)

        return {
            # legacy fields
            "run_id": run_id,
            "total_questions": total_questions,
            "recall_at_k": round(recall_at_k, 4),
            "precision_at_k": round(precision_at_k, 4),
            "mrr": round(mrr, 4),
            "ndcg_at_k": round(ndcg_at_k, 4),
            "latency_ms": round(latency_ms, 1),
            # 06-04 compatibility appendices
            "job_id": job_id,
            "status": status,
            "quality_comparable": quality_comparable,
            "metrics": metrics_block,
            "deprecation": DEPRECATION_META,
            "item_errors": item_errors or [],
        }

    async def get_run_report(
        self,
        db: AsyncSession,
        run_id: int,
    ) -> dict[str, Any]:
        """获取评测运行报告（legacy + deprecation metadata）"""
        run = await db.get(EvalRun, run_id)
        if not run:
            raise EvalServiceError(f"评测运行 ID={run_id} 不存在")

        result = await db.execute(select(EvalResult).where(EvalResult.run_id == run_id))
        eval_results = result.scalars().all()

        results_list = []
        error_cases = []
        for er in eval_results:
            item = {
                "id": er.id,
                "dataset_id": er.dataset_id,
                "recalled_chunks": er.recalled_chunks,
                "score": er.score,
                "metrics": er.metrics,
                "is_error_case": er.is_error_case,
            }
            results_list.append(item)
            if er.is_error_case:
                error_cases.append(item)

        snap = run.config_snapshot if isinstance(run.config_snapshot, dict) else {}
        job_id = snap.get("job_id")
        q_status = snap.get("quality_status") or "completed"
        quality_comparable = bool(snap.get("quality_comparable", False))

        return {
            "run": {
                "id": run.id,
                "run_name": run.run_name,
                "strategy": run.strategy,
                "novel_id": run.novel_id,
                "total_questions": run.total_questions,
                "recall_at_k": run.recall_at_k,
                "precision_at_k": run.precision_at_k,
                "mrr": run.mrr,
                "ndcg_at_k": run.ndcg_at_k,
                "latency_ms": run.latency_ms,
                "config_snapshot": run.config_snapshot,
                "created_at": run.created_at.isoformat() if run.created_at else None,
                # 06-04 fields
                "job_id": job_id,
                "status": q_status,
                "quality_comparable": quality_comparable,
            },
            "results": results_list,
            "error_cases": error_cases,
            "error_count": len(error_cases),
            "job_id": job_id,
            "status": q_status,
            "quality_comparable": quality_comparable,
            "metrics": None if not quality_comparable else snap.get("quality_metrics"),
            "deprecation": DEPRECATION_META,
        }

    # ── gold_chunks migration helpers ────────────────────────────────

    @staticmethod
    def classify_legacy_gold(
        gold_chunks: list[Any],
        *,
        id_to_hash: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        """Classify whether legacy gold_chunks can migrate to content hashes.

        Provable: every gold id maps via id_to_hash to a content hash.
        Otherwise quarantine (not quality-comparable).
        """
        ids: list[int] = []
        for g in gold_chunks or []:
            if isinstance(g, int):
                ids.append(g)
            elif isinstance(g, dict) and "chunk_id" in g:
                ids.append(int(g["chunk_id"]))
            else:
                return {
                    "status": "quarantined",
                    "reason": "unrecognized gold_chunks entry",
                    "evidence_hashes": [],
                    "quality_comparable": False,
                }

        if not ids:
            return {
                "status": "quarantined",
                "reason": "empty gold_chunks",
                "evidence_hashes": [],
                "quality_comparable": False,
            }

        if not id_to_hash:
            return {
                "status": "quarantined",
                "reason": "no id_to_hash mapping provided; cannot prove content-hash evidence",
                "evidence_hashes": [],
                "quality_comparable": False,
            }

        hashes: list[str] = []
        missing: list[int] = []
        for cid in ids:
            h = id_to_hash.get(cid)
            if not h:
                missing.append(cid)
            else:
                hashes.append(h)

        if missing:
            return {
                "status": "quarantined",
                "reason": f"unmapped chunk ids: {missing}",
                "evidence_hashes": hashes,
                "quality_comparable": False,
            }
        return {
            "status": "migrated",
            "reason": "all gold ids mapped to content hashes",
            "evidence_hashes": hashes,
            "quality_comparable": False,  # still needs full fixture freeze for gate
            "gold_chunk_ids": ids,
        }

    # ── 私有方法 ────────────────────────────────────────────────────

    async def _baseline_vector_search(
        self,
        query: str,
        novel_id: int,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        embeddings = await self.ai_service.embedding(texts=[query])
        if not embeddings:
            return []

        query_embedding = embeddings[0]
        results = await self.vector_store.search(
            novel_id=novel_id,
            query_embedding=query_embedding,
            top_k=top_k,
        )

        return [
            {
                "chunk_id": int(r["chunk_id"].replace("chunk_", "")),
                "score": r.get("score", 0.0),
            }
            for r in results
        ]

    async def _bm25_search(
        self,
        db: AsyncSession,
        query: str,
        novel_id: int,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        sql = text(
            """
            SELECT tc.id AS chunk_id,
                   ts_rank_cd(tc.search_vector, plainto_tsquery('simple', :query)) AS score
            FROM text_chunks tc
            WHERE tc.novel_id = :novel_id
              AND tc.search_vector @@ plainto_tsquery('simple', :query)
            ORDER BY score DESC
            LIMIT :limit
        """
        )
        result = await db.execute(
            sql, {"query": query, "novel_id": novel_id, "limit": top_k}
        )
        rows = result.fetchall()

        return [{"chunk_id": row[0], "score": float(row[1])} for row in rows]

    def _compute_metrics(
        self,
        gold_chunks: list,
        recalled_chunks: list,
        top_k: int = 5,
        recalled_scores: list[float] | None = None,
    ) -> dict[str, float]:
        gold_set = set(gold_chunks)
        recalled_set = set(recalled_chunks[:top_k])

        if not gold_set:
            return {
                "recall_at_k": 0.0,
                "precision_at_k": 0.0,
                "mrr": 0.0,
                "ndcg_at_k": 0.0,
            }

        hit_count = len(recalled_set & gold_set)

        recall = hit_count / len(gold_set)

        k = min(top_k, len(recalled_chunks))
        precision = hit_count / k if k > 0 and len(recalled_set) > 0 else 0.0

        mrr = 0.0
        for i, cid in enumerate(recalled_chunks[:top_k], start=1):
            if cid in gold_set:
                mrr = 1.0 / i
                break

        ndcg = self._compute_ndcg(
            gold_chunks, recalled_chunks[:top_k], top_k, recalled_scores
        )

        return {
            "recall_at_k": round(recall, 4),
            "precision_at_k": round(precision, 4),
            "mrr": round(mrr, 4),
            "ndcg_at_k": round(ndcg, 4),
        }

    @staticmethod
    def _compute_ndcg(
        gold_chunks: list,
        recalled_chunks: list,
        top_k: int = 5,
        scores: list[float] | None = None,
    ) -> float:
        gold_set = set(gold_chunks)
        k = min(top_k, len(recalled_chunks))
        if k == 0:
            return 0.0

        relevances = [1.0 if cid in gold_set else 0.0 for cid in recalled_chunks[:k]]

        dcg = 0.0
        for i, gain in enumerate(relevances, start=1):
            dcg += gain / math.log2(i + 1)

        ideal_gains = [1.0] * min(len(gold_set), k)
        ideal_gains += [0.0] * (k - len(ideal_gains))

        idcg = 0.0
        for i, gain in enumerate(ideal_gains, start=1):
            idcg += gain / math.log2(i + 1)

        return dcg / idcg if idcg > 0 else 0.0


# 全局单例
eval_service = EvalService()
