#!/usr/bin/env python3
"""Fixed explicit-version narrative-memory single-book qualification CLI.

Requires --owner-id --novel-id --version-id --fixture --policy and budget ack.
Verdict: qualified_candidate (exit 0) | blocked (exit 2). Never promotes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

FORBIDDEN_OPTIONS = frozenset(
    {
        "promote",
        "rollback",
        "active",
        "current",
        "default",
        "all-books",
        "embedding",
        "reader-chat",
        "chat",
        "cutover",
    }
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Single-book narrative-memory candidate qualification (no promotion)"
    )
    p.add_argument("--owner-id", type=int, required=True)
    p.add_argument("--novel-id", type=int, required=True)
    p.add_argument("--version-id", type=int, required=True)
    p.add_argument("--fixture", type=Path, required=True)
    p.add_argument("--policy", type=Path, required=True)
    p.add_argument(
        "--acknowledge-budget",
        action="store_true",
        required=True,
        help="Required acknowledgement of hard token/cost ceiling",
    )
    p.add_argument(
        "--require-version-rows",
        action="store_true",
        default=False,
        help="Require Phase 13/14 rows in DB (default: soft for deterministic dry fixtures)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip persistence; still emit canonical verdict JSON",
    )
    return p


def _reject_forbidden(argv: list[str]) -> None:
    joined = " ".join(argv).lower()
    for frag in FORBIDDEN_OPTIONS:
        if f"--{frag}" in joined or f" {frag}" in joined:
            raise SystemExit(f"forbidden option: {frag}")


async def _session_factory():
    from app.core.database import async_session_factory

    return async_session_factory


def _output_digest(payload: dict[str, Any]) -> str:
    from app.services.narrative_memory.qualification_contracts import stable_checksum

    body = {k: v for k, v in payload.items() if k != "output_digest"}
    return stable_checksum(body)


async def run_command(args: argparse.Namespace) -> int:
    from app.services.narrative_memory.qualification_contracts import (
        SCOPE_DISCLAIMER,
        QualificationVerdict,
    )
    from app.services.narrative_memory.qualification_fixtures import (
        load_frozen_bundle,
        preflight_execution_gates,
        PreflightBlocked,
    )
    from app.services.narrative_memory.qualification_metrics import (
        metric_report_checksum,
    )
    from app.services.narrative_memory.qualification_runner import run_qualification
    from app.services.narrative_memory.qualification_verdict import evaluate_verdict

    fixture, policy, _, _ = load_frozen_bundle(args.fixture, args.policy)

    # Bind CLI IDs (must match fixture or override for operator-selected book)
    if (
        fixture.owner_id != args.owner_id
        or fixture.novel_id != args.novel_id
        or fixture.version_id != args.version_id
    ):
        fixture = fixture.model_copy(
            update={
                "owner_id": args.owner_id,
                "novel_id": args.novel_id,
                "version_id": args.version_id,
            }
        )

    preflight_reasons: list[str] = []
    try:
        preflight_execution_gates(
            fixture=fixture,
            policy=policy,
            price_known=True,
            phase13_wip=False,
            build_complete=True,
        )
    except PreflightBlocked as exc:
        preflight_reasons = list(exc.reason_codes)

    pointer_before_digest = "0" * 64
    pointer_after_digest = "0" * 64
    verifier_checksum = "0" * 64
    verifier_reasons: list[str] = []
    run_id = None
    report_row_id = None

    if args.dry_run:
        result = await run_qualification(
            None,
            fixture,
            policy,
            preflight_reasons=preflight_reasons,
            reuse_ok=True,
            pointer_before_digest=pointer_before_digest,
            pointer_after_digest=pointer_after_digest,
            verifier_checksum=verifier_checksum,
        )
        report = result.report
        assert report is not None
    else:
        from app.services.narrative_memory.qualification_repository import (
            create_run,
            insert_case_result,
            seal_report,
        )
        from app.services.narrative_memory.qualification_verifier import (
            snapshot_production_pointers,
            pointer_digest,
            verify_qualification,
        )

        factory = await _session_factory()
        async with factory() as session:
            before = await snapshot_production_pointers(session)
            pointer_before_digest = pointer_digest(before)

            run = await create_run(
                session,
                fixture=fixture,
                policy=policy,
                pointer_before_digest=pointer_before_digest,
            )
            run_id = run.id

            result = await run_qualification(
                session,
                fixture,
                policy,
                preflight_reasons=preflight_reasons,
                reuse_ok=True,
                pointer_before_digest=pointer_before_digest,
                pointer_after_digest=pointer_before_digest,  # temp
                verifier_checksum=verifier_checksum,
            )
            for art in result.artifacts:
                await insert_case_result(session, run=run, artifact=art)
            await session.commit()

        # Fresh observer session
        async with factory() as session:
            after = await snapshot_production_pointers(session)
            pointer_after_digest = pointer_digest(after)
            vres = await verify_qualification(
                session,
                fixture=fixture,
                policy=policy,
                pointer_before=before,
                pointer_after=after,
                require_version_rows=args.require_version_rows,
            )
            verifier_checksum = vres.verifier_checksum
            verifier_reasons = list(vres.reasons)

            # Re-evaluate with verifier outcomes
            report = evaluate_verdict(
                policy=policy,
                fixture_checksum=fixture.checksum(),
                policy_checksum=policy.checksum(),
                metric_cells=result.metric_cells,
                preflight_reasons=list(preflight_reasons) + verifier_reasons,
                scope_ok="version_missing" not in verifier_reasons
                and "source_snapshot_mismatch" not in verifier_reasons,
                structure_ok="manifest_missing_or_mismatch" not in verifier_reasons,
                build_complete="build_incomplete" not in verifier_reasons,
                reuse_ok=True,
                paired_comparable=True,
                pointer_equal=pointer_before_digest == pointer_after_digest,
                pointer_before_digest=pointer_before_digest,
                pointer_after_digest=pointer_after_digest,
                verifier_checksum=verifier_checksum,
            )

            metric_cs = metric_report_checksum(result.metric_cells)
            command_payload = {
                "owner_id": args.owner_id,
                "novel_id": args.novel_id,
                "version_id": args.version_id,
                "fixture_checksum": fixture.checksum(),
                "policy_checksum": policy.checksum(),
                "verdict": report.verdict.value,
                "reason_codes": list(report.reason_codes),
            }
            # provisional digest without output_digest
            provisional = {
                **command_payload,
                "qualification_kind": "single_book_candidate",
                "disclaimer": SCOPE_DISCLAIMER,
                "run_id": run_id,
                "pointer_before_digest": pointer_before_digest,
                "pointer_after_digest": pointer_after_digest,
                "verifier_checksum": verifier_checksum,
                "metric_payload_checksum": metric_cs,
            }
            out_digest = _output_digest(provisional)

            # load run again
            from sqlalchemy import select
            from app.models.narrative_memory_qualification import (
                NarrativeMemoryQualificationRun,
            )

            run = await session.scalar(
                select(NarrativeMemoryQualificationRun).where(
                    NarrativeMemoryQualificationRun.id == run_id
                )
            )
            assert run is not None
            sealed = await seal_report(
                session,
                run=run,
                report=report,
                metric_payload_checksum=metric_cs,
                verifier_checksum=verifier_checksum,
                pointer_after_digest=pointer_after_digest,
                command_payload=command_payload,
                output_digest=out_digest,
            )
            report_row_id = sealed.id
            await session.commit()

    assert report is not None
    payload = {
        "qualification_kind": "single_book_candidate",
        "verdict": report.verdict.value,
        "reason_codes": list(report.reason_codes),
        "fixture_checksum": fixture.checksum(),
        "policy_checksum": policy.checksum(),
        "owner_id": args.owner_id,
        "novel_id": args.novel_id,
        "version_id": args.version_id,
        "run_id": run_id,
        "report_id": report_row_id,
        "pointer_before_digest": report.pointer_before_digest
        or pointer_before_digest,
        "pointer_after_digest": report.pointer_after_digest or pointer_after_digest,
        "verifier_checksum": report.verifier_checksum or verifier_checksum,
        "disclaimer": SCOPE_DISCLAIMER,
    }
    payload["output_digest"] = _output_digest(payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if report.verdict == QualificationVerdict.QUALIFIED_CANDIDATE:
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _reject_forbidden(argv)
    parser = _build_parser()
    # argparse required=True on store_true is awkward; enforce manually
    if "--acknowledge-budget" not in argv:
        print(
            json.dumps(
                {
                    "verdict": "blocked",
                    "reason_codes": ["missing_budget_acknowledgement"],
                    "qualification_kind": "single_book_candidate",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    # strip required from action for parse
    for action in parser._actions:
        if action.dest == "acknowledge_budget":
            action.required = False
    args = parser.parse_args(argv)
    if not args.acknowledge_budget:
        return 1
    try:
        return asyncio.run(run_command(args))
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "verdict": "blocked",
                    "reason_codes": [f"command_error:{type(exc).__name__}"],
                    "qualification_kind": "single_book_candidate",
                    "detail": str(exc)[:200],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        print(
            json.dumps(
                {
                    "verdict": "blocked",
                    "reason_codes": [f"command_error:{type(exc).__name__}"],
                    "qualification_kind": "single_book_candidate",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
