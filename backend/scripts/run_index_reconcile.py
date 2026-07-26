"""Reconcile raw text_chunks against the novel's Chroma collection (Phase 24-02).

Usage:
    python scripts/run_index_reconcile.py <novel_id> [--repair]

Prints a JSON report (missing / orphan / manifest binding / repair outcome).
Exit code 0 when consistent (after repair, if requested), 1 otherwise.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session_factory  # noqa: E402
from app.services.indexing_reconcile import IndexReconcileService  # noqa: E402


async def _run(novel_id: int, repair: bool) -> dict:
    service = IndexReconcileService()
    async with async_session_factory() as db:
        report = await service.reconcile_novel(db, novel_id, repair=repair)
        if repair and not report["consistent"]:
            # 修复后复查一次，报告最终一致性
            recheck = await service.reconcile_novel(db, novel_id, repair=False)
            report["post_repair"] = {
                "consistent": recheck["consistent"],
                "missing_count": recheck["missing"]["count"],
                "orphan_count": recheck["orphans"]["count"],
                "novel_status": recheck["novel_status"],
            }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile DB text_chunks vs Chroma vectors for one novel"
    )
    parser.add_argument("novel_id", type=int, help="小说 ID")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="修复：补 embed missing、删 orphan 向量（写 reconcile_repair journal）",
    )
    args = parser.parse_args()

    report = asyncio.run(_run(args.novel_id, args.repair))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    if args.repair and "post_repair" in report:
        return 0 if report["post_repair"]["consistent"] else 1
    return 0 if report["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
