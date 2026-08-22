#!/usr/bin/env python3
"""Create and execute an independent live calibration suite for the RAG judge."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.eval import CalibrationCase, EvidenceRef
from app.services.ai_service import _extract_token_usage, ai_service
from app.services.rag_fixture import (
    DEFAULT_SIGNING_SECRET,
    freeze_calibration_suite,
    run_judge_calibration,
    schema_contract_hash,
    verify_calibration_suite,
)
from scripts.run_novel91_live_quality_review import (
    DEFAULT_MODEL,
    LIVE_JUDGE_POLICY_TEXT,
    LIVE_JUDGE_PROMPT_HASH,
    make_live_judge_lineage,
)

JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
ALLOWED = {"accept", "partial", "reject", "contradictory", "no_answer", "hard_negative"}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ref(text: str) -> EvidenceRef:
    digest = _hash(text)
    return EvidenceRef(
        chunk_content_hash=digest,
        start_offset=0,
        end_offset=len(text),
        quote_hash=digest,
        quote_text=text,
    )


def _calibration_cases() -> list[CalibrationCase]:
    """Use unambiguous synthetic cases, independent of the novel-91 benchmark."""
    return [
        CalibrationCase(
            case_id="live-supported",
            category="supported",
            question="谁站在学院门前？",
            candidate_answer="路明非站在学院门前。",
            evidence_refs=[_ref("路明非站在学院门前。")],
            gold_verdict="accept",
        ),
        CalibrationCase(
            case_id="live-partial",
            category="partial",
            question="谁站在学院门前并拿着雨伞？",
            candidate_answer="路明非站在学院门前并拿着雨伞。",
            evidence_refs=[_ref("路明非站在学院门前。")],
            gold_verdict="partial",
        ),
        CalibrationCase(
            case_id="live-unsupported",
            category="unsupported",
            question="他的银行余额是多少？",
            candidate_answer="他有一百万元。",
            evidence_refs=[],
            gold_verdict="reject",
            critical=True,
        ),
        CalibrationCase(
            case_id="live-contradictory",
            category="contradictory",
            question="他在哪里？",
            candidate_answer="他在纽约街头。",
            evidence_refs=[_ref("他站在学院门前。")],
            gold_verdict="contradictory",
            critical=True,
        ),
        CalibrationCase(
            case_id="live-no-answer",
            category="no_answer",
            question="他的电话号码是多少？",
            candidate_answer="不知道。",
            evidence_refs=[],
            gold_verdict="no_answer",
            critical=True,
        ),
        CalibrationCase(
            case_id="live-hard-negative",
            category="hard_negative",
            question="雨是否证明他拔刀？",
            candidate_answer="雨证明他拔刀。",
            evidence_refs=[_ref("今夜的雨很大。")],
            gold_verdict="hard_negative",
            critical=True,
        ),
        CalibrationCase(
            case_id="live-equivalent",
            category="equivalent_evidence",
            question="学院门前是谁？",
            candidate_answer="路明非。",
            evidence_refs=[_ref("路明非站在学院门前。")],
            gold_verdict="accept",
        ),
    ]


def _prompt(case: dict[str, Any]) -> str:
    evidence = "\n---\n".join(
        str(ref.get("quote_text") or "")
        for ref in case.get("evidence_refs", [])
        if ref.get("quote_text")
    ) or "（无证据）"
    return f"""{LIVE_JUDGE_POLICY_TEXT}

问题：{case['question']}
候选答案：{case['candidate_answer']}
证据原文：
{evidence}

只输出 JSON：{{"classification":"accept|partial|reject|contradictory|no_answer|hard_negative"}}。不要输出 gold verdict、category 或解释。"""


def _classification(text: str) -> str:
    match = JSON_BLOCK_RE.search(text or "")
    if not match:
        raise ValueError("no_json_object")
    value = json.loads(match.group(0))
    label = value.get("classification") if isinstance(value, dict) else None
    if label not in ALLOWED:
        raise ValueError("classification_invalid")
    return label


async def _judge_one(case: dict[str, Any], repeat: int, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        try:
            response = await ai_service.chat(
                [
                    {"role": "system", "content": LIVE_JUDGE_POLICY_TEXT},
                    {"role": "user", "content": _prompt(case)},
                ],
                model=DEFAULT_MODEL,
                temperature=0,
                max_tokens=100,
                task_type="rag_quality_live_calibration",
            )
            raw = response.choices[0].message.content or ""
            input_tokens, output_tokens = _extract_token_usage(response)
            return {
                "case_id": case["case_id"],
                "repeat": repeat,
                "status": "reviewed",
                "classification": _classification(raw),
                "response_hash": _hash(raw),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
        except Exception as exc:  # noqa: BLE001 - preserve per-call audit evidence
            return {
                "case_id": case["case_id"],
                "repeat": repeat,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
            }


async def _run(args: argparse.Namespace) -> int:
    cases = _calibration_cases()
    suite = freeze_calibration_suite(
        suite_id="novel91-live-judge-calibration-20260728-v2",
        domain="calibration-novel91-live",
        cases=cases,
        prompt_hash=LIVE_JUDGE_PROMPT_HASH,
        schema_hash=schema_contract_hash(),
        secret=DEFAULT_SIGNING_SECRET,
    )
    if not verify_calibration_suite(suite, DEFAULT_SIGNING_SECRET):
        raise RuntimeError("calibration suite signature verification failed")
    suite_path = Path(args.suite_output)
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite_path.write_text(json.dumps(suite.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")

    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    jobs = [
        _judge_one(case.model_dump(mode="json"), repeat, semaphore)
        for case in suite.cases
        for repeat in range(1, args.repeats + 1)
    ]
    results = await asyncio.gather(*jobs)
    errors = [item for item in results if item["status"] == "error"]
    if errors:
        report = {
            "status": "live_calibration_failed",
            "suite_hash": suite.suite_hash,
            "errors": errors,
            "results": results,
        }
        Path(args.report_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "errors": len(errors)}, ensure_ascii=False))
        return 1

    by_case: dict[str, deque[str]] = defaultdict(deque)
    for item in results:
        by_case[item["case_id"]].append(item["classification"])

    lineage = make_live_judge_lineage(datetime.now(timezone.utc))

    def judge_fn(case, _lineage):
        return by_case[case.case_id].popleft()

    calibration = run_judge_calibration(
        suite,
        lineage,
        judge_fn=judge_fn,
        repeats=args.repeats,
        secret=DEFAULT_SIGNING_SECRET,
    )
    report_path = Path(args.report_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(calibration.model_dump(by_alias=True, mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    input_tokens = sum(int(item.get("input_tokens", 0)) for item in results)
    output_tokens = sum(int(item.get("output_tokens", 0)) for item in results)
    audit = {
        "schema_version": "rag-quality-live-calibration-audit.v1",
        "status": calibration.status,
        "suite": str(suite_path),
        "suite_hash": suite.suite_hash,
        "report": str(report_path),
        "judge_lineage": calibration.judge_lineage.model_dump(by_alias=True, mode="json"),
        "counts": {"cases": len(suite.cases), "repeats": args.repeats, "calls": len(results)},
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "results": results,
    }
    audit_path = Path(args.audit_output)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": calibration.status,
        "consistency": calibration.consistency,
        "critical_false_accept": calibration.critical_false_accept,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "suite_hash": suite.suite_hash,
    }, ensure_ascii=False))
    return 0 if calibration.status == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-output", default="evals/calibration/novel91-live-calibration-20260728-v1.json")
    parser.add_argument("--report-output", default="evals/results/phase28/novel91-live-calibration-report.json")
    parser.add_argument("--audit-output", default="evals/results/phase28/novel91-live-calibration-audit.json")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
