"""Execute frozen evaluation against an exact PostgreSQL/Chroma candidate."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session_factory  # noqa: E402
from app.models.knowledge_unit import NarrativeIndexBuild  # noqa: E402
from app.services.ai_service import ai_service  # noqa: E402
from app.services.vector_store import vector_store  # noqa: E402
from app.services.knowledge_units.eval import (  # noqa: E402
    candidate_retriever,
    evaluate_candidate,
    load_fixture,
)


async def _run(args) -> dict:
    secret = os.environ.get("NARRATIVE_EVAL_SIGNING_SECRET", "")
    if not secret:
        raise RuntimeError("NARRATIVE_EVAL_SIGNING_SECRET is required")
    async with async_session_factory() as db:
        build = await db.get(NarrativeIndexBuild, args.build_id)
        if build is None:
            raise ValueError("candidate build not found")
        return await evaluate_candidate(
            load_fixture(args.fixture),
            build=build,
            retrieve=candidate_retriever(
                build, vector_store=vector_store, ai_service=ai_service
            ),
            signing_secret=secret,
            latency_budget_ms=args.latency_budget_ms,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--build-id", type=int, required=True)
    parser.add_argument("--latency-budget-ms", type=float, default=1000.0)
    parser.add_argument(
        "--output", required=True, help="immutable per-query signed run JSON"
    )
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    output = Path(args.output)
    if output.exists():
        raise RuntimeError("eval output is immutable and already exists")
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
