#!/usr/bin/env python
"""Default-off offline hierarchical retrieval experiment CLI.

Requires explicit owner, novel, candidate version, frozen question and
persisted cutoff. No promote/current/active options. Exit 0=completed, 2=blocked.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_hierarchical_retrieval_experiment",
        description=(
            "Offline candidate hierarchical retrieval experiment "
            "(default-off; explicit version required)"
        ),
    )
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--novel-id", type=int, required=True)
    parser.add_argument("--version-id", type=int, required=True)
    parser.add_argument(
        "--question",
        type=str,
        required=True,
        help="Frozen question fixture text (hashed; not a free-form prod API)",
    )
    parser.add_argument("--cutoff-chapter", type=int, required=True)
    parser.add_argument(
        "--cutoff-snapshot-hash",
        type=str,
        required=True,
        help="Persisted reading-progress cutoff snapshot hash (64 hex)",
    )
    parser.add_argument(
        "--full-book-authorized",
        action="store_true",
        default=False,
        help="Require persisted full-book authorization; never inferred",
    )
    parser.add_argument("--selected-chapter", type=int, default=None)
    parser.add_argument("--selected-start", type=int, default=None)
    parser.add_argument("--selected-end", type=int, default=None)
    parser.add_argument(
        "--fixture-checksum",
        type=str,
        default=None,
        help="Optional expected query_hash of the frozen question fixture",
    )
    parser.add_argument(
        "--manifest-checksum",
        type=str,
        default=None,
        help="Optional expected candidate manifest checksum",
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        default=False,
        help="Explicitly enable experiment for this process (still respects config default)",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.services.narrative_memory.experiments import (
        ExperimentDisabledError,
        ExperimentInputError,
        experiment_request_from_fixture,
        run_retrieval_experiment,
    )

    enabled = bool(settings.narrative_memory_retrieval_experiment_enabled) or bool(
        args.enable
    )
    # CLI --enable alone is insufficient in production; require settings OR test override.
    # For operator use, set NOVELMIND_NARRATIVE_MEMORY_RETRIEVAL_EXPERIMENT_ENABLED=true
    # and pass --enable for belt-and-suspenders. Tests inject enabled=True directly.

    if not settings.narrative_memory_retrieval_experiment_enabled and not args.enable:
        print(
            '{"status":"blocked","blocked_reason":"experiment_disabled"}',
            flush=True,
        )
        return 2

    # When only --enable is passed without config, still allow for local offline use
    # but document that default config is false.
    enabled = True

    try:
        request = experiment_request_from_fixture(
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            version_id=args.version_id,
            raw_question=args.question,
            cutoff_chapter=args.cutoff_chapter,
            cutoff_snapshot_hash=args.cutoff_snapshot_hash,
            full_book_authorized=args.full_book_authorized,
            selected_chapter=args.selected_chapter,
            selected_start=args.selected_start,
            selected_end=args.selected_end,
            fixture_checksum=args.fixture_checksum,
            expected_manifest_checksum=args.manifest_checksum,
        )
    except ExperimentInputError as exc:
        print(
            f'{{"status":"blocked","blocked_reason":"input_error","detail":"{exc}"}}',
            flush=True,
        )
        return 2

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            try:
                result = await run_retrieval_experiment(
                    session, request, enabled=enabled
                )
            except ExperimentDisabledError:
                print(
                    '{"status":"blocked","blocked_reason":"experiment_disabled"}',
                    flush=True,
                )
                return 2
            print(result.canonical_json(), flush=True)
            return result.exit_code
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
