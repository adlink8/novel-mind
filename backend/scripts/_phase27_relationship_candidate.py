#!/usr/bin/env python3
"""Run the real relationship semantic worker against a candidate version.

This deliberately does not call a relationship active-pointer promotion path.
The existing accepted source judgments and their evidence packages are the only
inputs; the worker decides whether each relation is establish/change/end and
the normal interval/evidence gates remain authoritative.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.core.database import async_session_factory
from app.models.analysis import AnalysisVersion
from app.services.relationships.judgment import RelationshipJudgmentService
from app.services.relationships.worker import RelationshipObservationWorker


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner-id", type=int, required=True)
    p.add_argument("--novel-id", type=int, required=True)
    p.add_argument("--analysis-version-id", type=int, required=True)
    p.add_argument("--candidate-version-id", type=int, default=None)
    p.add_argument("--build-run-id", type=int, default=None)
    p.add_argument(
        "--intake-kind",
        choices=("llm_judgment", "timeline_seed_backfill"),
        default=None,
    )
    return p


async def _clone_version(owner_id: int, novel_id: int, parent_id: int) -> int:
    async with async_session_factory.begin() as db:
        parent = await db.scalar(
            select(AnalysisVersion).where(
                AnalysisVersion.id == parent_id,
                AnalysisVersion.owner_id == owner_id,
                AnalysisVersion.novel_id == novel_id,
            )
        )
        if parent is None:
            raise ValueError("base analysis version is outside the requested scope")
        candidate = AnalysisVersion(
            owner_id=owner_id,
            novel_id=novel_id,
            parent_version_id=parent.id,
            version_key=f"phase27-rel-{uuid.uuid4().hex}",
            status="candidate",
            source_snapshot_hash=parent.source_snapshot_hash,
            hierarchy_build_id=parent.hierarchy_build_id,
            hierarchy_checksum=parent.hierarchy_checksum,
            prompt_hash=parent.prompt_hash,
            schema_hash=parent.schema_hash,
            model_lineage=dict(parent.model_lineage or {}),
            decoding_hash=parent.decoding_hash,
            config_hash=parent.config_hash,
            price_snapshot=dict(parent.price_snapshot or {}),
            manifest={},
        )
        db.add(candidate)
        await db.flush()
        return candidate.id


async def main() -> int:
    args = _parser().parse_args()
    candidate_version_id = args.candidate_version_id or await _clone_version(
        args.owner_id, args.novel_id, args.analysis_version_id
    )
    from app.services.ai_service import AIService

    model_name = AIService.litellm_model_name(
        settings.chat_provider, settings.default_chat_model
    )
    worker = RelationshipObservationWorker(
        judgment_service=RelationshipJudgmentService(model_name=model_name)
    )
    async with async_session_factory() as db:
        result = await worker.run(
            db,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            analysis_version_id=candidate_version_id,
            build_run_id=args.build_run_id,
            intake_kind=args.intake_kind,
        )
        await db.commit()
    payload = {
        "status": result.status,
        "analysis_version_id": candidate_version_id,
        "build_run_id": result.build_run_id,
        "candidate_count": result.candidate_count,
        "judgment_count": result.judgment_count,
        "accepted_count": result.accepted_count,
        "review_count": result.review_count,
        "rejected_count": result.rejected_count,
        "provider_calls": result.provider_calls,
        "call_skipped": result.call_skipped,
        "identity_reviews": result.identity_reviews,
        "rejections": result.rejections,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
