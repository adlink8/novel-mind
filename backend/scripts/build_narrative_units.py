"""Build narrative units from an immutable accepted-judgment snapshot."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session_factory  # noqa: E402
from app.services.knowledge_units.canonicalize import narrative_canonicalizer  # noqa: E402
from app.services.knowledge_units.materialize import narrative_unit_materializer  # noqa: E402


async def run(snapshot_id: int, *, write: bool) -> dict:
    async with async_session_factory() as db:
        materialized = await narrative_unit_materializer.materialize_snapshot(
            db, snapshot_id=snapshot_id, write=write
        )
        result = {"materialize": materialized.__dict__ if hasattr(materialized, "__dict__") else {
            "snapshot_id": materialized.snapshot_id,
            "created": materialized.created,
            "reused": materialized.reused,
            "rejected": list(materialized.rejected),
            "manifest_checksum": materialized.manifest_checksum,
        }}
        if write:
            canonical = await narrative_canonicalizer.canonicalize_snapshot(
                db, snapshot_id=snapshot_id
            )
            result["canonicalize"] = {
                "canonicalized": canonical.canonicalized,
                "reused": canonical.reused,
                "review_proposals": list(canonical.review_proposals),
                "hard_negative_false_merges": canonical.hard_negative_false_merges,
                "checksum": canonical.checksum,
            }
            await db.commit()
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-id", type=int, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.snapshot_id, write=args.write)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
