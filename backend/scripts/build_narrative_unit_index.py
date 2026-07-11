"""Build an immutable narrative-unit candidate collection."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session_factory  # noqa: E402
from app.services.knowledge_units.indexing import narrative_indexing_service  # noqa: E402


async def run(build_id: int | None, snapshot_id: int | None) -> dict:
    async with async_session_factory() as db:
        if build_id is None:
            build = await narrative_indexing_service.prepare_build(
                db, snapshot_id=snapshot_id
            )
            build_id = build.id
        report = await narrative_indexing_service.build_candidate(db, build_id=build_id)
        await db.commit()
        return {name: getattr(report, name) for name in report.__dataclass_fields__}


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--build-id", type=int)
    source.add_argument("--snapshot-id", type=int)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(args.build_id, args.snapshot_id)), indent=2, default=list
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
