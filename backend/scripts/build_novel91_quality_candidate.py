#!/usr/bin/env python3
"""Build a content-hash RAG quality candidate from formal novel 91 chunks.

This is deliberately a candidate fixture, not an assertion of human-confirmed
gold.  It removes DB auto-increment IDs from the truth path while preserving a
clear semantic-review boundary for Phase 28 qualification.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import async_session_factory
from app.models.text_chunk import TextChunk
from app.services.rag_fixture import (
    DEFAULT_SIGNING_SECRET,
    build_source_snapshot,
    compute_fixture_hash,
    default_stub_judge,
    freeze_eval_case,
    make_evidence_ref,
    run_deterministic_checks,
    schema_contract_hash,
    stable_hash,
    verify_frozen_case,
    verify_source_snapshot,
)
from scripts.run_novel91_live_quality_review import make_live_judge_lineage
from app.schemas.eval import (
    Claim,
    EquivalentEvidenceSet,
    EvalCase,
    ModelLineage,
)


OWNER_ID = 2
NOVEL_ID = 91
FIXED_CREATED_AT = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)


def source_phrase(item: dict) -> str:
    """Read the full deterministic source sentence used as candidate gold."""

    phrase = str(item.get("reference_answer") or "").strip()
    if not phrase:
        raise ValueError("candidate missing reference_answer; regenerate candidates")
    return phrase


def lineage(*, role: str, revision: str) -> ModelLineage:
    return ModelLineage(
        provider="novelmind",
        model_family=f"deterministic_{role}",
        model_id=f"novel91-{role}-v1",
        **{"weights/revision": revision},
        endpoint_class="offline_candidate",
        prompt_hash=stable_hash({"role": role, "version": "novel91-v1"}),
        prompt_version=f"novel91.{role}.v1",
        schema_hash=schema_contract_hash(),
        runtime="offline",
        started_at=FIXED_CREATED_AT,
    )


async def build(output_path: Path) -> dict:
    candidates_path = Path("evals/novel91_eval_candidates.json")
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    if len(candidates) != 100 or any(c.get("novel_id") != NOVEL_ID for c in candidates):
        raise ValueError("expected exactly 100 novel 91 candidates")

    ids = sorted({int(chunk_id) for item in candidates for chunk_id in item["gold_chunks"]})
    async with async_session_factory() as db:
        rows = list(
            (
                await db.scalars(
                    select(TextChunk).where(TextChunk.id.in_(ids))
                )
            ).all()
        )
        total_chunks = int(
            await db.scalar(
                select(func.count()).select_from(TextChunk).where(
                    TextChunk.novel_id == NOVEL_ID
                )
            )
            or 0
        )

    by_id = {int(row.id): row for row in rows}
    missing = [chunk_id for chunk_id in ids if chunk_id not in by_id]
    if missing:
        raise ValueError(f"gold chunk IDs missing from novel 91: {missing[:10]}")

    texts: list[str] = []
    id_to_hash: dict[int, str] = {}
    seen_hashes: set[str] = set()
    for chunk_id in ids:
        content = str(by_id[chunk_id].content)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        id_to_hash[chunk_id] = content_hash
        if content_hash not in seen_hashes:
            texts.append(content)
            seen_hashes.add(content_hash)

    snapshot = build_source_snapshot(
        owner_id=OWNER_ID,
        work_id=NOVEL_ID,
        texts=texts,
        version="novel91-quality-candidate-v1",
        snapshot_id="novel91-quality-candidate-20260728-v1",
        secret=DEFAULT_SIGNING_SECRET,
        created_at=FIXED_CREATED_AT,
    )
    if not verify_source_snapshot(snapshot, DEFAULT_SIGNING_SECRET):
        raise ValueError("source snapshot signature verification failed")

    hash_to_text = {chunk.content_hash: chunk.text or "" for chunk in snapshot.chunks}
    generator = lineage(role="generator", revision="rule-seed-91-v1")
    judge = make_live_judge_lineage(FIXED_CREATED_AT)
    cases: list[EvalCase] = []
    failed: list[dict] = []

    for index, item in enumerate(candidates, start=1):
        chunk_id = int(item["gold_chunks"][0])
        content_hash = id_to_hash[chunk_id]
        content = hash_to_text[content_hash]
        phrase = source_phrase(item)
        start = content.find(phrase)
        if start < 0:
            failed.append({"index": index, "chunk_id": chunk_id, "phrase": phrase})
            continue
        ref = make_evidence_ref(snapshot, content_hash, start, start + len(phrase))
        case = EvalCase(
            case_id=f"novel91-candidate-{index:03d}",
            snapshot_hash=snapshot.manifest_hash,
            question=item["question"],
            case_type="answerable",
            claims=[
                Claim(
                    claim_id=f"novel91-claim-{index:03d}",
                    text=f"原文证据片段：{phrase}",
                    critical=True,
                    evidence_set_ids=["s1"],
                )
            ],
            equivalent_evidence_sets=[EquivalentEvidenceSet(set_id="s1", refs=[ref])],
            reference_answer=phrase,
            generator_lineage=generator,
            judge_lineage=judge,
            status="deterministic_validation",
        )
        checks = run_deterministic_checks(
            case,
            snapshot,
            expected_owner_id=OWNER_ID,
            expected_work_id=NOVEL_ID,
        )
        if not checks.all_passed:
            failed.append(
                {
                    "index": index,
                    "chunk_id": chunk_id,
                    "failed_checks": [d.name for d in checks.details if not d.passed],
                }
            )
            continue
        case.deterministic_checks = checks
        case.judge_fixture_verdict = default_stub_judge(case, snapshot, judge)
        frozen = freeze_eval_case(case, DEFAULT_SIGNING_SECRET)
        if not verify_frozen_case(frozen, DEFAULT_SIGNING_SECRET):
            raise ValueError(f"frozen case signature verification failed: {frozen.case_id}")
        cases.append(frozen)

    if failed or len(cases) != 100:
        raise ValueError(f"candidate fixture validation failed: {len(failed)} failures")

    manifest_pairs = sorted((str(chunk_id), content_hash) for chunk_id, content_hash in id_to_hash.items())
    payload = {
        "schema_version": "rag-quality.v1",
        "suite_type": "candidate",
        "domain": "fiction",
        "novel_id": NOVEL_ID,
        "owner_id": OWNER_ID,
        "candidate_status": "candidate_frozen_requires_semantic_review",
        "snapshot": snapshot.model_dump(mode="json"),
        "cases": [case.model_dump(mode="json", by_alias=True) for case in cases],
        "source_audit": {
            "formal_novel_id": NOVEL_ID,
            "formal_chunk_count": total_chunks,
            "gold_source_chunk_count": len(ids),
            "unique_snapshot_chunk_count": len(snapshot.chunks),
            "db_id_to_hash_manifest": stable_hash(manifest_pairs),
            "legacy_db_ids_are_not_case_truth": True,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "cases": len(cases),
        "snapshot_chunks": len(snapshot.chunks),
        "formal_chunks": total_chunks,
        "snapshot_hash": snapshot.manifest_hash,
        "candidate_fixture_hashes": [compute_fixture_hash(case) for case in cases[:3]],
        "output": str(output_path),
        "status": payload["candidate_status"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/results/phase28/novel91-quality-candidate.json"),
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(build(args.output)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
