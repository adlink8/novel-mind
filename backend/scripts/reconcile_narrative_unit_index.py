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


async def resolve_active_build_id(
    db,
    *,
    pointer_id: int | None = None,
    owner_id: int | None = None,
    novel_id: int | None = None,
    domain: str | None = None,
) -> int:
    query = select(NarrativeActivePointer)
    if pointer_id is not None:
        query = query.where(NarrativeActivePointer.id == pointer_id)
    if owner_id is not None:
        query = query.where(NarrativeActivePointer.owner_id == owner_id)
    if novel_id is not None:
        query = query.where(NarrativeActivePointer.novel_id == novel_id)
    if domain is not None:
        query = query.where(NarrativeActivePointer.domain_profile == domain)
    pointers = list((await db.scalars(query)).all())
    if len(pointers) != 1:
        raise ValueError(
            "--active scope must select exactly one active pointer"
        )
    return pointers[0].build_id


async def _run(
    build_id: int | None,
    active: bool,
    *,
    pointer_id: int | None = None,
    owner_id: int | None = None,
    novel_id: int | None = None,
    domain: str | None = None,
) -> dict:
    async with async_session_factory() as db:
        if active:
            build_id = await resolve_active_build_id(
                db,
                pointer_id=pointer_id,
                owner_id=owner_id,
                novel_id=novel_id,
                domain=domain,
            )
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
    parser.add_argument("--pointer-id", type=int)
    parser.add_argument("--owner-id", type=int)
    parser.add_argument("--novel-id", type=int)
    parser.add_argument("--domain", choices=("fiction", "history"))
    args = parser.parse_args()
    scoped = any(
        value is not None
        for value in (args.pointer_id, args.owner_id, args.novel_id, args.domain)
    )
    if scoped and not args.active:
        parser.error("pointer scope options require --active")
    report = asyncio.run(
        _run(
            args.build_id,
            args.active,
            pointer_id=args.pointer_id,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            domain=args.domain,
        )
    )
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
