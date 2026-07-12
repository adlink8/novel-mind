#!/usr/bin/env python3
"""Import validated RAG evaluation candidates from a JSON file."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.eval import EvalDataset
from app.models.novel import Novel


async def import_questions(path: Path, novel_id: int) -> int:
    candidates = json.loads(path.read_text(encoding="utf-8"))
    selected = [item for item in candidates if item.get("novel_id") == novel_id]
    if not selected:
        raise ValueError(f"文件中没有 novel_id={novel_id} 的测试题")

    async with async_session_factory() as db:
        if await db.get(Novel, novel_id) is None:
            raise ValueError(f"小说 ID={novel_id} 不存在")

        existing = await db.execute(
            select(EvalDataset.id).where(EvalDataset.novel_id == novel_id).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(
                f"小说 ID={novel_id} 已有评测数据；请先人工确认是否需要清理"
            )

        for item in selected:
            db.add(
                EvalDataset(
                    novel_id=novel_id,
                    question=item["question"],
                    question_type=item["question_type"],
                    difficulty=item.get("difficulty", "medium"),
                    gold_chunks=item.get("gold_chunks", []),
                    expected_points=item.get("expected_points", []),
                    must_not_say=item.get("must_not_say", []),
                    status="candidate",
                    created_by=item.get("created_by", "ai"),
                )
            )
        await db.commit()
    return len(selected)


async def main() -> None:
    parser = argparse.ArgumentParser(description="导入 RAG 评测候选题")
    parser.add_argument("--novel-id", type=int, required=True)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("evals/novel_eval_candidates.json"),
    )
    args = parser.parse_args()

    count = await import_questions(args.input, args.novel_id)
    print(f"[OK] 已导入 {count} 条 candidate 测试题")


if __name__ == "__main__":
    asyncio.run(main())
