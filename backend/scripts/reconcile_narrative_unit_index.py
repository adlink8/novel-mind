"""Reconcile a narrative build against exported actual collection items."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session_factory  # noqa: E402
from app.services.knowledge_units.reconcile import reconcile_build  # noqa: E402


async def _run(build_id: int, actual_file: str) -> dict:
    actual = json.loads(Path(actual_file).read_text(encoding="utf-8"))
    async with async_session_factory() as db:
        report = await reconcile_build(db, build_id=build_id, actual_items=actual)
        return {name: getattr(report, name) for name in report.__dataclass_fields__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-id", type=int, required=True)
    parser.add_argument("--actual-file", required=True)
    args = parser.parse_args()
    report = asyncio.run(_run(args.build_id, args.actual_file))
    print(json.dumps(report, indent=2, default=list))
    return 0 if not any(report[key] for key in ("missing", "orphan", "duplicate", "wrong_owner", "deleted", "deprecated")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
