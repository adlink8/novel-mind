#!/usr/bin/env python
"""Operator CLI for explicit-version narrative-memory candidate dry-runs.

Commands: start | status | cancel | resume
No promote / rollback / current / default / all-books options.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running as script from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_narrative_memory_build",
        description="Candidate-only narrative memory builder (explicit version required)",
    )
    parser.add_argument("command", choices=("start", "status", "cancel", "resume"))
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--novel-id", type=int, required=True)
    parser.add_argument("--version-id", type=int, required=True)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit canonical JSON on stdout",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    # Lazy imports so --help works without full app wiring.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.services.narrative_memory.audit_pg import PostgresAssetInventorySource
    from app.services.narrative_memory.builder_contracts import (
        BudgetPolicy,
        ModelDeploymentSnapshot,
        RunPolicy,
        StageKind,
    )
    from app.services.narrative_memory.builder_repository import BuilderRepository
    from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
    from app.services.narrative_memory.contracts import ModelLineage

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class _NoopTransport:
        async def complete(self, **kwargs):
            raise RuntimeError(
                "production CLI requires an injected controlled transport in tests; "
                "use status/cancel only without a worker transport adapter"
            )

    deployment = ModelDeploymentSnapshot(
        provider="noop",
        model="noop",
        deployment="noop",
        revision="1",
        supports_structured_output=True,
        input_price_per_million="1.0",
        output_price_per_million="1.0",
    )
    inventory = PostgresAssetInventorySource(sessions)
    worker = NarrativeMemoryBuilderWorker(
        sessions,
        inventory_source=inventory,
        transport=_NoopTransport(),
        deployment=deployment,
    )

    if args.command == "status":
        async with sessions() as session:
            repo = BuilderRepository(session)
            run = await repo.get_run(
                owner_id=args.owner_id,
                novel_id=args.novel_id,
                version_id=args.version_id,
            )
            if run is None:
                payload = {"status": "missing", "run_id": None}
                print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
                await engine.dispose()
                return 2
            stages = await repo.list_stages(run.id)
            payload = {
                "run_id": run.id,
                "status": run.status,
                "status_reason": run.status_reason,
                "version_id": run.version_id,
                "stages": [
                    {
                        "stage_key": s.stage_key,
                        "status": s.status,
                        "artifact_checksum": s.artifact_checksum,
                    }
                    for s in stages
                ],
            }
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            await engine.dispose()
            return 0

    if args.command == "cancel":
        result = await worker.cancel(
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            version_id=args.version_id,
        )
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "status": result.status,
                    "status_reason": result.status_reason,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        await engine.dispose()
        return 0

    # start / resume need a real controlled environment; document exit 3 when
    # production transport is not configured. Integration tests inject transport.
    hex64 = "a" * 64
    policy = RunPolicy(
        policy_version="builder-policy.v1",
        stage_order=(
            StageKind.CHAPTER_STATE,
            StageKind.ARC_VOLUME_PLAN,
            StageKind.ARC_VOLUME_AGGREGATE,
            StageKind.GLOBAL_AGGREGATE,
            StageKind.MANIFEST_VALIDATION,
        ),
        max_schema_repairs=1,
        chapter_concurrency=1,
        arc_window_size=3,
        budget=BudgetPolicy(
            max_calls=100,
            max_input_tokens=1_000_000,
            max_output_tokens=1_000_000,
            max_cost_usd="100.0",
        ),
        prompt_hash=hex64,
        schema_hash=hex64,
        model_lineage=ModelLineage(
            provider="noop", model="noop", deployment="noop", revision="1"
        ),
        decoding_hash=hex64,
        config_hash=hex64,
        policy_hash=hex64,
    )
    if args.command == "start":
        try:
            run_id = await worker.start_run(
                owner_id=args.owner_id,
                novel_id=args.novel_id,
                version_id=args.version_id,
                run_policy=policy,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {"error": type(exc).__name__, "message": str(exc)},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            await engine.dispose()
            return 1
        print(
            json.dumps(
                {"run_id": run_id, "status": "pending"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        await engine.dispose()
        return 0

    # resume
    try:
        result = await worker.process_run(
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            version_id=args.version_id,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"error": type(exc).__name__, "message": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        await engine.dispose()
        return 1
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "status_reason": result.status_reason,
                "transport_calls": result.transport_calls,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    await engine.dispose()
    return 0 if result.status in {"completed", "partial", "running", "pending"} else 1


def main() -> None:
    args = _parser().parse_args()
    # Reject forbidden flags that must never appear.
    forbidden = {"--promote", "--rollback", "--current", "--default", "--all-books"}
    if forbidden.intersection(sys.argv):
        print(
            json.dumps(
                {"error": "forbidden_flag"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
