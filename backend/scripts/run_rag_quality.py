#!/usr/bin/env python3
"""CLI: run durable RAG quality evaluation against signed fixtures (06-04).

Examples:
  python scripts/run_rag_quality.py --fixture evals/fixtures/rag-quality-benchmark.v1.json
  python scripts/run_rag_quality.py --fixture ... --baseline evals/results/baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.eval import CalibrationReport, EvalCase, SourceSnapshot
from app.services.rag_fixture import DEFAULT_SIGNING_SECRET, load_json
from app.services.rag_quality import (
    default_healthy,
    load_policy,
    make_baseline_from_metrics,
    probe_ollama_health,
    run_quality_evaluation,
)
from app.services.rag_quality_worker import RagQualityWorker, QualityJobStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RAG quality evaluation (06-04)")
    parser.add_argument(
        "--fixture",
        default="evals/fixtures/rag-quality-benchmark.v1.json",
        help="Signed benchmark fixture suite JSON",
    )
    parser.add_argument(
        "--calibration",
        default=None,
        help="Optional calibration report JSON (status=passed required)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Baseline metrics JSON; if omitted uses self-establish then re-run",
    )
    parser.add_argument(
        "--output",
        default="evals/results/rag-quality-report.json",
        help="Output report path",
    )
    parser.add_argument(
        "--owner-id",
        type=int,
        default=1,
        help="Owner id for durable job",
    )
    parser.add_argument(
        "--live-health",
        action="store_true",
        help="Probe local Ollama; outage => blocked_dependency",
    )
    parser.add_argument(
        "--durable",
        action="store_true",
        help="Use durable worker (lease/checkpoint)",
    )
    args = parser.parse_args()

    data = load_json(args.fixture)
    snap = SourceSnapshot.model_validate(data["snapshot"])
    cases = [EvalCase.model_validate(c) for c in data["cases"]]
    g = cases[0].generator_lineage if cases else None
    j = cases[0].judge_lineage if cases else None

    if args.calibration:
        cal_raw = load_json(args.calibration)
        cal = CalibrationReport.model_validate(cal_raw)
    else:
        # Synthetic passed calibration bound to case judge lineage for offline CLI
        if j is None:
            print("[ERR] no judge lineage in fixtures and no --calibration", file=sys.stderr)
            return 2
        cal = CalibrationReport(
            suite_hash="a" * 64,
            suite_signature="b" * 64,
            prompt_hash=j.prompt_hash,
            schema_hash=j.schema_hash,
            judge_lineage=j,
            domain="calibration-synthetic",
            repeats=3,
            confusion_matrix={},
            critical_false_accept=0,
            consistency=1.0,
            status="passed",
            metrics={"consistency": 1.0, "critical_false_accept": 0},
            quality_comparable=False,
        )

    health = probe_ollama_health() if args.live_health else default_healthy()
    policy = load_policy()
    print(f"[OK] policy={policy.get('version')} health_ok={health.get('ok')}")

    if args.baseline:
        baseline = load_json(args.baseline)
    else:
        print("[INFO] no baseline; establishing from current stub/SUT run")
        establish = run_quality_evaluation(
            snapshot=snap,
            cases=cases,
            generator_lineage=g,
            judge_lineage=j,
            calibration_report=cal,
            baseline={
                "context_recall_at_5_mean": 0.0,
                "answer_relevance_mean": 0.0,
                "cost_usd_total": 999.0,
            },
            health=health,
            secret=DEFAULT_SIGNING_SECRET,
        )
        if not establish.get("metrics"):
            print(
                f"[FAIL] cannot establish baseline: status={establish.get('status')} "
                f"reason={establish.get('reason')}",
                file=sys.stderr,
            )
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(establish, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return 1
        baseline = make_baseline_from_metrics(establish["metrics"])

    if args.durable:
        worker = RagQualityWorker(store=QualityJobStore(), secret=DEFAULT_SIGNING_SECRET)
        job = worker.create_job(
            owner_id=args.owner_id,
            snapshot=snap,
            cases=cases,
            generator_lineage=g,
            judge_lineage=j,
            calibration_report=cal,
            baseline=baseline,
            health=health,
        )
        job = worker.resume(job.job_id, owner_id=args.owner_id)
        report = job.report or job.to_public()
        print(f"[OK] job={job.job_id} status={job.status} comparable={job.quality_comparable}")
    else:
        report = run_quality_evaluation(
            snapshot=snap,
            cases=cases,
            generator_lineage=g,
            judge_lineage=j,
            calibration_report=cal,
            baseline=baseline,
            health=health,
            secret=DEFAULT_SIGNING_SECRET,
        )
        print(
            f"[OK] status={report.get('status')} comparable={report.get('quality_comparable')}"
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[OK] wrote {out}")
    status = report.get("status") if isinstance(report, dict) else None
    return 0 if status in {"passed", "qualified"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
