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
from sqlalchemy import select  # noqa: E402
from app.models.knowledge_unit import NarrativeActivePointer, NarrativeIndexBuild  # noqa: E402
from app.services.vector_store import vector_store  # noqa: E402
from app.services.knowledge_units.reconcile import (  # noqa: E402
    read_actual_collection,
    reconcile_build,
)


async def _run(build_id: int | None, active: bool) -> dict:
    async with async_session_factory() as db:
        if active:
            pointers = list((await db.scalars(select(NarrativeActivePointer))).all())
            if len(pointers) != 1:
                raise ValueError(
                    "--active requires exactly one active pointer; scope the database"
                )
            build_id = pointers[0].build_id
        build = await db.get(NarrativeIndexBuild, build_id)
        if build is None:
            raise ValueError("build not found")
        actual = await asyncio.to_thread(lambda: None) or await read_actual_collection(
            build, vector_store
        )
        report = await reconcile_build(db, build_id=build_id, actual_items=actual)
        return {
            **{name: getattr(report, name) for name in report.__dataclass_fields__},
            "collection": build.collection_name,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--build-id", type=int)
    source.add_argument("--active", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(_run(args.build_id, args.active))
    print(json.dumps(report, indent=2, default=list))
    return (
        0
        if not any(
            report[key]
            for key in (
                "missing",
                "orphan",
                "duplicate",
                "wrong_build",
                "wrong_owner",
                "deleted",
                "deprecated",
            )
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
