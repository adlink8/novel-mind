"""Emit one canonical, read-only narrative-memory asset eligibility report."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.services.narrative_memory.audit import audit_assets  # noqa: E402
from app.services.narrative_memory.audit_contracts import EligibilityReport  # noqa: E402
from app.services.narrative_memory.audit_pg import PostgresAuditSource  # noqa: E402


async def collect_report(
    *, owner_id: int, novel_id: int, session: AsyncSession | None = None
) -> EligibilityReport:
    if session is not None:
        return await audit_assets(
            PostgresAuditSource(session), owner_id=owner_id, novel_id=novel_id
        )
    async with async_session_factory() as owned_session:
        return await audit_assets(
            PostgresAuditSource(owned_session), owner_id=owner_id, novel_id=novel_id
        )


def canonical_report_json(report: EligibilityReport) -> str:
    return json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def report_exit_code(report: EligibilityReport) -> int:
    return 0 if report.provider_calls_allowed else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读检查 v0.8 分层记忆资产资格")
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--novel-id", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.owner_id < 1 or args.novel_id < 1:
        raise SystemExit("owner-id 和 novel-id 必须为正整数")
    report = asyncio.run(collect_report(owner_id=args.owner_id, novel_id=args.novel_id))
    print(canonical_report_json(report))
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
