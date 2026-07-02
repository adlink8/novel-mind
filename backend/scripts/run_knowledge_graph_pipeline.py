#!/usr/bin/env python3
"""Run the knowledge graph candidate package and LLM judgment pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import async_session_factory
from app.models.knowledge import KnowledgeExtractionRun, KnowledgeRelationCandidate
from app.models.novel import Novel
from app.services.knowledge.candidates import candidate_recall_service
from app.services.knowledge.evidence import evidence_ref_from_package_item
from app.services.knowledge.llm_judge import PROMPT_VERSION, llm_judge_service


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build bounded knowledge candidate packages and LLM judgments."
    )
    parser.add_argument("--novel-id", type=int, required=True, help="Novel ID")
    parser.add_argument(
        "--domain-profile",
        choices=["fiction", "history"],
        default="fiction",
        help="Ontology/domain profile",
    )
    parser.add_argument("--ontology-profile", help="Override ontology profile")
    parser.add_argument("--query", help="Optional BM25/vector recall query")
    parser.add_argument("--limit", type=int, default=5, help="Hard cap on LLM calls")
    parser.add_argument("--owner-id", type=int, help="Optional owner isolation check")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Do not persist rows")
    group.add_argument("--write", action="store_true", help="Persist run audit rows")
    return parser


async def _persist_candidate(
    db,
    *,
    run: KnowledgeExtractionRun,
    draft,
    package: dict[str, Any],
) -> KnowledgeRelationCandidate:
    candidate = KnowledgeRelationCandidate(
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        run_id=run.id,
        domain_profile=draft.domain_profile,
        relation_type=draft.relation_type,
        source_kind=draft.source_kind,
        source_id=draft.source_id,
        target_kind=draft.target_kind,
        target_id=draft.target_id,
        recall_signals=draft.recall_signals,
        package_snapshot=package,
        evidence_refs=package["allowed_evidence_ids"],
        status="candidate",
    )
    db.add(candidate)
    await db.flush()
    package["candidate"]["candidate_id"] = candidate.id
    candidate.package_snapshot = package
    return candidate


async def _persist_unique_evidence_refs(
    db,
    *,
    run: KnowledgeExtractionRun,
    package: dict[str, Any],
    seen_refs: set[str],
) -> None:
    for item in package["evidence"]:
        ref_key = item["evidence_id"]
        if ref_key in seen_refs:
            continue
        db.add(
            evidence_ref_from_package_item(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                run_id=run.id,
                item=item,
            )
        )
        seen_refs.add(ref_key)
    await db.flush()


async def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    dry_run = args.dry_run or not args.write
    if args.limit < 1:
        raise ValueError("--limit must be >= 1")

    async with async_session_factory() as db:
        novel = await db.get(Novel, args.novel_id)
        if novel is None:
            raise ValueError(f"Novel ID {args.novel_id} does not exist")
        if args.owner_id is not None and novel.owner_id != args.owner_id:
            raise ValueError("Novel owner does not match --owner-id")

        packages = await candidate_recall_service.build_candidate_packages(
            db,
            novel_id=args.novel_id,
            domain_profile=args.domain_profile,
            ontology_profile=args.ontology_profile,
            owner_id=args.owner_id,
            query=args.query,
            limit=args.limit,
        )

        mode = "dry-run" if dry_run else "write"
        summary: dict[str, Any] = {
            "mode": mode,
            "novel_id": args.novel_id,
            "novel_title": novel.title,
            "domain_profile": args.domain_profile,
            "ontology_profile": args.ontology_profile or f"{args.domain_profile}.v1",
            "candidate_count": len(packages),
            "judgment_count": 0,
            "statuses": {},
            "first_package": packages[0][2] if packages else None,
            "judgments": [],
        }

        run: KnowledgeExtractionRun | None = None
        if not dry_run:
            run = KnowledgeExtractionRun(
                owner_id=novel.owner_id,
                novel_id=novel.id,
                run_name=f"knowledge_{args.domain_profile}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
                domain_profile=args.domain_profile,
                ontology_profile=args.ontology_profile or f"{args.domain_profile}.v1",
                status="running",
                prompt_version=PROMPT_VERSION,
                config_snapshot={
                    "limit": args.limit,
                    "query": args.query,
                    "mode": mode,
                    "package_version": "knowledge-evidence-package.v1",
                },
            )
            db.add(run)
            await db.flush()
            summary["run_id"] = run.id

        seen_refs: set[str] = set()
        for draft, _evidence_chunks, package in packages[: args.limit]:
            candidate = None
            if run is not None:
                await _persist_unique_evidence_refs(
                    db,
                    run=run,
                    package=package,
                    seen_refs=seen_refs,
                )
                candidate = await _persist_candidate(
                    db,
                    run=run,
                    draft=draft,
                    package=package,
                )

            result = await llm_judge_service.judge_package(
                package,
                persist=run is not None,
                db=db if run is not None else None,
                candidate=candidate,
            )
            summary["judgment_count"] += 1
            summary["statuses"][result.status] = summary["statuses"].get(result.status, 0) + 1
            summary["judgments"].append(
                {
                    "candidate_id": result.candidate_id,
                    "status": result.status,
                    "gate_status": result.gate_status,
                    "relation_type": result.relation_type,
                    "confidence": result.confidence,
                    "evidence_refs": result.evidence_refs,
                    "gate_failures": result.gate_failures,
                }
            )

        if run is not None:
            run.candidate_count = len(packages)
            run.judgment_count = summary["judgment_count"]
            run.review_count = summary["statuses"].get("needs_human_review", 0) + summary[
                "statuses"
            ].get("blocked", 0)
            run.rejected_count = summary["statuses"].get("schema_failed", 0) + summary[
                "statuses"
            ].get("evidence_failed", 0)
            run.status = "completed" if "blocked" not in summary["statuses"] else "failed"
            if run.status == "failed":
                run.error_detail = "One or more LLM judgments were blocked"
            await db.commit()

        return summary


async def _main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        summary = await run_pipeline(args)
    except Exception as exc:
        print(f"[ERROR] Knowledge graph pipeline failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
