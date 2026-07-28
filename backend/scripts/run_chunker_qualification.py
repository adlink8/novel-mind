"""CLI: run chunker A/B qualification and emit QualifiedChunkerEvidence JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.chunking.builds import InMemoryBuildStore, create_candidate_build
from app.services.chunking.eval import run_ab_qualification
from app.services.chunking.release_verifier import verify_and_qualify
from app.services.rag_fixture import stable_hash


def main() -> int:
    p = argparse.ArgumentParser(description="Chunker A/B qualification (Phase 07-06)")
    p.add_argument("--novel-id", type=int, default=1)
    p.add_argument("--snapshot-hash", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    snap = args.snapshot_hash or ("a" * 64)
    policy = stable_hash({"policy": "rag-quality-policy.v1"})
    store = InMemoryBuildStore()
    chapters = [
        {
            "chapter_id": 1,
            "chapter_number": 1,
            "content": "资格测试章节正文。" * 40,
        }
    ]
    a = create_candidate_build(
        store,
        novel_id=args.novel_id,
        chapters=chapters,
        source_snapshot_hash=snap,
        chunker_name="rule-baseline",
        chunker_version="1.0.0",
        force_full=True,
    )
    store.active[args.novel_id] = a.build_id
    b = create_candidate_build(
        store,
        novel_id=args.novel_id,
        chapters=chapters,
        source_snapshot_hash=snap,
        chunker_name="hierarchical-v1",
        chunker_version="1.0.0",
        parent_build_id=a.build_id,
        force_full=True,
    )
    # active still A
    assert store.active[args.novel_id] == a.build_id

    report = run_ab_qualification(
        store,
        novel_id=args.novel_id,
        source_snapshot_hash=snap,
        policy_hash=policy,
        baseline_build_id=a.build_id,
        candidate_build_id=b.build_id,
    )
    evidence = verify_and_qualify(
        store,
        ab_report=report,
        candidate_build_id=b.build_id,
        policy_hash=policy,
    )
    out = {
        "ab_report_status": report.get("status"),
        "quality_comparable": report.get("quality_comparable"),
        "evidence": evidence.model_dump(),
        "active_build_id": store.active.get(args.novel_id),
        "candidate_build_id": b.build_id,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if evidence.status == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
