#!/usr/bin/env python3
"""
RAG 评测 CLI 脚本 — 命令行触发评测对比

使用方式:
  cd backend
  python scripts/run_rag_eval.py --strategy hybrid_search --novel-id 1 --output evals/results/
  python scripts/run_rag_eval.py --strategy baseline_vector --novel-id 1 --output evals/results/

功能:
  - 从 evals/novel_eval_candidates.json 加载测试题
  - 按策略执行检索评测
  - 输出 JSON 结果 + Markdown 报告
  - 错误案例导出（recall=0 的条目）
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.eval import EvalDataset
from app.models.novel import Novel
from app.services.eval_service import eval_service


async def load_or_create_datasets(db, novel_id: int, candidates_path: str) -> list[int]:
    """加载或创建评测数据集，返回 dataset ID 列表"""
    # 检查是否已有 confirmed 的测试题
    result = await db.execute(
        select(EvalDataset).where(
            EvalDataset.novel_id == novel_id,
            EvalDataset.status.in_(["confirmed", "candidate"]),
        )
    )
    existing = result.scalars().all()

    if existing:
        print(f"[OK] 使用已有数据集: {len(existing)} 条")
        return [ds.id for ds in existing]

    # 从 JSON 文件导入
    path = Path(candidates_path)
    if not path.exists():
        print(f"[WARN] 候选文件不存在: {candidates_path}")
        print("[INFO] 请先运行: python scripts/generate_eval_candidates.py --novel-id {novel_id}")
        return []

    with open(path, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    ids = []
    for c in candidates:
        if c.get("novel_id") != novel_id:
            continue
        ds = EvalDataset(
            novel_id=novel_id,
            question=c["question"],
            question_type=c.get("question_type", "original_text"),
            difficulty=c.get("difficulty", "medium"),
            gold_chunks=c.get("gold_chunks", []),
            expected_points=c.get("expected_points", []),
            must_not_say=c.get("must_not_say", []),
            status=c.get("status", "candidate"),
            created_by=c.get("created_by", "auto"),
        )
        db.add(ds)
        await db.flush()
        ids.append(ds.id)

    await db.commit()
    print(f"[OK] 导入 {len(ids)} 条测试题")
    return ids


def generate_markdown_report(
    report: dict,
    strategy: str,
    novel_title: str,
    output_dir: Path,
) -> str:
    """生成 Markdown 评测报告"""
    data = report["data"]
    run = data["run"]
    results = data["results"]
    error_cases = data.get("error_cases", [])

    lines = [
        "# RAG 评测报告",
        "",
        f"**生成时间**: {datetime.now().isoformat()}",
        f"**小说**: {novel_title}",
        f"**策略**: {strategy}",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总题数 | {run['total_questions']} |",
        f"| Recall@{run['config_snapshot'].get('top_k', 5)} | {run['recall_at_k']:.2%} |",
    ]

    if run.get("precision_at_k") is not None:
        lines.append(f"| Precision@{run['config_snapshot'].get('top_k', 5)} | {run['precision_at_k']:.2%} |")
    if run.get("mrr") is not None:
        lines.append(f"| MRR | {run['mrr']:.4f} |")
    if run.get("ndcg_at_k") is not None:
        lines.append(f"| NDCG@{run['config_snapshot'].get('top_k', 5)} | {run['ndcg_at_k']:.4f} |")
    if run.get("latency_ms") is not None:
        lines.append(f"| 总延迟 | {run['latency_ms']:.1f} ms |")

    lines += [
        "",
        "## 结果分布",
        "",
        f"- 正常: {len(results) - len(error_cases)}",
        f"- 错误案例 (recall=0): {len(error_cases)}",
    ]

    # 按题型统计
    by_type = defaultdict(lambda: {"count": 0, "recall_sum": 0.0})
    for r in results:
        m = r.get("metrics", {})
        by_type["total"]["count"] += 1
        by_type["total"]["recall_sum"] += m.get("recall_at_k", 0.0)

    if by_type["total"]["count"] > 0:
        lines += [
            "",
            f"平均 Recall: {by_type['total']['recall_sum'] / by_type['total']['count']:.2%}",
        ]

    if error_cases:
        lines += [
            "",
            "## 错误案例",
            "",
        ]
        for i, ec in enumerate(error_cases[:10], 1):
            lines.append(f"{i}. dataset_id={ec['dataset_id']}, recalled={ec['recalled_chunks']}")

    # 写入文件
    report_path = output_dir / f"eval_report_{strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_text = "\n".join(lines)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"[OK] Markdown 报告: {report_path}")
    return report_text


async def main():
    parser = argparse.ArgumentParser(description="RAG 评测运行脚本")
    parser.add_argument("--strategy", required=True, choices=["baseline_vector", "hybrid_search"],
                        help="检索策略")
    parser.add_argument("--novel-id", type=int, required=True, help="小说 ID")
    parser.add_argument("--output", default="evals/results/", help="输出目录")
    parser.add_argument("--candidates", default="evals/novel_eval_candidates.json",
                        help="候选测试题 JSON 文件")
    parser.add_argument("--top-k", type=int, default=5, help="召回数量")
    parser.add_argument("--run-name", help="评测名称（默认自动生成）")
    parser.add_argument("--skip-existing", action="store_true",
                        help="如果已有 confirmed 数据，跳过导入")
    args = parser.parse_args()

    strategy = args.strategy
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_name = args.run_name or f"{strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    async with async_session_factory() as db:
        # 验证小说
        novel = await db.get(Novel, args.novel_id)
        if not novel:
            print(f"[ERROR] 小说 ID={args.novel_id} 不存在")
            sys.exit(1)

        # 加载数据集
        dataset_ids = await load_or_create_datasets(db, args.novel_id, args.candidates)
        if not dataset_ids:
            print("[ERROR] 没有可用的测试题")
            sys.exit(1)

        print(f"\n[INFO] 开始评测: 小说={novel.title}, 策略={strategy}, 题数={len(dataset_ids)}, top_k={args.top_k}")

        # 执行评测
        try:
            summary = await eval_service.run_eval(
                db=db,
                run_name=run_name,
                strategy=strategy,
                novel_id=args.novel_id,
                dataset_ids=dataset_ids,
                top_k=args.top_k,
            )
        except Exception as e:
            print(f"[ERROR] 评测失败: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # JSON 结果
        json_path = output_dir / f"eval_result_{strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        # Markdown 报告
        report = await eval_service.get_run_report(db, run_id=summary["run_id"])
        generate_markdown_report(
            {"data": report}, strategy, novel.title, output_dir,
        )

        # 控制台输出
        print(f"\n{'='*60}")
        print(f"评测完成: {novel.title} ({strategy})")
        print(f"  总题数: {summary['total_questions']}")
        print(f"  Recall@{args.top_k}: {summary['recall_at_k']:.2%}")
        if summary.get('mrr'):
            print(f"  MRR: {summary['mrr']:.4f}")
        if summary.get('ndcg_at_k'):
            print(f"  NDCG@{args.top_k}: {summary['ndcg_at_k']:.4f}")
        print(f"  总延迟: {summary['latency_ms']:.1f} ms")
        print(f"  JSON:  {json_path}")
        print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
