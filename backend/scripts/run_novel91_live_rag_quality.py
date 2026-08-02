#!/usr/bin/env python3
"""Run the novel-91 RAG path against formal PostgreSQL and live Vertex models.

This is an evidence-producing SUT run. It does not mutate eval rows, pointers,
or production data. Retrieval is the formal BM25 implementation; answers are
generated from retrieved text only; the already calibrated live judge scores
the resulting answer three times per case.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import async_session_factory
from app.models.text_chunk import TextChunk
from app.schemas.eval import CalibrationReport
from app.services.ai_service import _extract_token_usage, ai_service
from app.services.eval_service import eval_service
from app.services.rag_fixture import load_json, schema_contract_hash, stable_hash
from scripts.run_novel91_live_quality_review import (
    DEFAULT_MODEL,
    LIVE_JUDGE_POLICY_TEXT,
    _parse_verdict,
    make_live_judge_lineage,
)

NOVEL_ID = 91
TOP_K = 5
ANSWER_PROMPT_VERSION = "novel91.live.answer-generator.v1"
ANSWER_POLICY = "只根据提供的检索上下文回答问题；先寻找与问题直接相关的事实并简洁复述；上下文确实不足时才回答‘未知’，不要补充常识或猜测。"
ANSWER_PROMPT_HASH = stable_hash(
    {"prompt_version": ANSWER_PROMPT_VERSION, "policy": ANSWER_POLICY}
)
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _answer_lineage(started_at: datetime) -> dict[str, Any]:
    return {
        "provider": "vertex_google",
        "model_family": "vertex_gemini_live_answer_generator",
        "model_id": DEFAULT_MODEL,
        "weights/revision": "vertex-gemini-3.5-flash-lite-2026-07-28",
        "endpoint_class": "live_external_sut",
        "prompt_hash": ANSWER_PROMPT_HASH,
        "prompt_version": ANSWER_PROMPT_VERSION,
        "schema_hash": schema_contract_hash(),
        "runtime": "vertex_live",
        "started_at": started_at.isoformat(),
    }


def _expected_hashes(case: dict[str, Any]) -> set[str]:
    return {
        str(ref["chunk_content_hash"])
        for evidence_set in case.get("equivalent_evidence_sets", [])
        for ref in evidence_set.get("refs", [])
    }


def _answer_prompt(question: str, contexts: list[str]) -> str:
    joined = "\n\n--- 检索上下文 ---\n\n".join(contexts) or "（没有召回上下文）"
    return f"""{ANSWER_POLICY}

问题：{question}
{joined}

只输出最终答案，不要输出分析过程。"""


def _judge_prompt(question: str, answer: str, reference: str, contexts: list[str]) -> str:
    joined = "\n\n--- 检索上下文 ---\n\n".join(contexts) or "（没有召回上下文）"
    return f"""{LIVE_JUDGE_POLICY_TEXT}

问题：{question}
参考答案：{reference}
候选答案：{answer}
实际检索上下文：
{joined}

审查规则：判断候选答案是否被实际检索上下文支持。不要因为参考答案存在就接受。
只输出 JSON：{{"classification":"accept|partial|reject|contradictory|no_answer|hard_negative","accepted":true或false,"faithfulness":1到4,"coverage":1到4,"sufficiency":1到4,"critical_ambiguity":0或1,"reason_codes":["UPPER_SNAKE_CASE"]}}"""


async def _chat_with_retry(messages: list[dict[str, str]], *, max_tokens: int, task_type: str) -> Any:
    """Retry only transient Vertex quota responses, with a bounded backoff."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return await ai_service.chat(
                messages,
                model=DEFAULT_MODEL,
                temperature=0,
                max_tokens=max_tokens,
                task_type=task_type,
            )
        except Exception as exc:  # noqa: BLE001 - provider error is classified below
            last_error = exc
            if "429" not in str(exc) or attempt == 2:
                raise
            await asyncio.sleep(2**attempt)
    raise last_error or RuntimeError("chat_retry_exhausted")


async def _retrieve_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        for case in cases:
            retrieved = await eval_service._bm25_search(
                db, case["question"], NOVEL_ID, top_k=TOP_K
            )
            ids = [int(item["chunk_id"]) for item in retrieved]
            rows = list(
                (
                    await db.scalars(
                        select(TextChunk).where(TextChunk.id.in_(ids))
                    )
                ).all()
            ) if ids else []
            by_id = {int(row.id): row for row in rows}
            ordered = []
            for item in retrieved:
                row = by_id.get(int(item["chunk_id"]))
                if row is None:
                    continue
                content = str(row.content)
                ordered.append(
                    {
                        "chunk_id": int(row.id),
                        "content_hash": _hash(content),
                        "score": float(item.get("score", 0.0)),
                        "text_length": len(content),
                        "text": content,
                    }
                )
            results.append(
                {
                    "case_id": case["case_id"],
                    "retrieved": ordered,
                    "retrieval_hit": bool(_expected_hashes(case) & {r["content_hash"] for r in ordered}),
                }
            )
    return results


async def _answer_and_judge(
    case: dict[str, Any],
    retrieval: dict[str, Any],
    *,
    repeats: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    contexts = [item["text"] for item in retrieval["retrieved"]]
    started = time.perf_counter()
    async with semaphore:
        try:
            answer_response = await _chat_with_retry(
                [
                    {"role": "system", "content": ANSWER_POLICY},
                    {"role": "user", "content": _answer_prompt(case["question"], contexts)},
                ],
                max_tokens=300,
                task_type="rag_quality_live_answer",
            )
            answer = str(answer_response.choices[0].message.content or "").strip()
            answer_input, answer_output = _extract_token_usage(answer_response)
            judge_results = []
            for repeat in range(1, repeats + 1):
                judge_response = await _chat_with_retry(
                    [
                        {"role": "system", "content": LIVE_JUDGE_POLICY_TEXT},
                        {
                            "role": "user",
                            "content": _judge_prompt(
                                case["question"],
                                answer,
                                case.get("reference_answer", ""),
                                contexts,
                            ),
                        },
                    ],
                    max_tokens=220,
                    task_type="rag_quality_live_sut_judge",
                )
                raw = str(judge_response.choices[0].message.content or "")
                verdict = _parse_verdict(raw)
                judge_input, judge_output = _extract_token_usage(judge_response)
                judge_results.append(
                    {
                        "repeat": repeat,
                        "verdict": verdict,
                        "response_hash": _hash(raw),
                        "input_tokens": judge_input,
                        "output_tokens": judge_output,
                    }
                )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                "case_id": case["case_id"],
                "status": "reviewed",
                "retrieval_hit": retrieval["retrieval_hit"],
                "retrieved": [
                    {k: v for k, v in item.items() if k != "text"}
                    for item in retrieval["retrieved"]
                ],
                "answer_hash": _hash(answer),
                "answer_length": len(answer),
                "answer_input_tokens": answer_input,
                "answer_output_tokens": answer_output,
                "judge_results": judge_results,
                "latency_ms": elapsed_ms,
            }
        except Exception as exc:  # noqa: BLE001 - preserve per-case evidence
            return {
                "case_id": case["case_id"],
                "status": "error",
                "retrieval_hit": retrieval["retrieval_hit"],
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }


def _consistency(items: list[dict[str, Any]]) -> float:
    scores = []
    for item in items:
        labels = [j["verdict"]["classification"] for j in item.get("judge_results", [])]
        if labels:
            scores.append(max(Counter(labels).values()) / len(labels))
    return sum(scores) / len(scores) if scores else 0.0


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    fixture_path = Path(args.fixture)
    fixture = load_json(fixture_path)
    calibration = CalibrationReport.model_validate(load_json(Path(args.calibration)))
    expected_judge = make_live_judge_lineage(datetime.now(timezone.utc))
    lineage_match = (
        calibration.status == "passed"
        and calibration.judge_lineage.weights_revision == expected_judge.weights_revision
        and calibration.judge_lineage.prompt_hash == expected_judge.prompt_hash
        and calibration.judge_lineage.schema_hash == expected_judge.schema_hash
    )
    if not lineage_match:
        raise RuntimeError("calibration_report_not_compatible_with_live_judge")
    cases = list(fixture.get("cases") or [])
    if args.max_cases:
        cases = cases[: args.max_cases]
    retrievals = await _retrieve_cases(cases)
    by_case = {item["case_id"]: item for item in retrievals}
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    results = await asyncio.gather(
        *[
            _answer_and_judge(
                case,
                by_case[case["case_id"]],
                repeats=args.repeats,
                semaphore=semaphore,
            )
            for case in cases
        ]
    )
    reviewed = [item for item in results if item["status"] == "reviewed"]
    errors = [item for item in results if item["status"] == "error"]
    judge_rows = [j for item in reviewed for j in item.get("judge_results", [])]
    labels = Counter(j["verdict"]["classification"] for j in judge_rows)
    accepted = sum(
        1
        for item in reviewed
        if any(j["verdict"]["classification"] == "accept" for j in item.get("judge_results", []))
    )
    critical_ambiguous = sum(
        1
        for j in judge_rows
        if j["verdict"]["critical_ambiguity"] == 1
    )
    input_tokens = sum(int(item.get("answer_input_tokens", 0)) for item in reviewed)
    output_tokens = sum(int(item.get("answer_output_tokens", 0)) for item in reviewed)
    input_tokens += sum(int(j.get("input_tokens", 0)) for j in judge_rows)
    output_tokens += sum(int(j.get("output_tokens", 0)) for j in judge_rows)
    pricing_path = Path(args.pricing_snapshot)
    pricing = load_json(pricing_path)
    estimated_cost = (
        input_tokens * float(pricing["input_usd_per_1m_tokens"])
        + output_tokens * float(pricing["output_usd_per_1m_tokens"])
    ) / 1_000_000
    retrieval_hit_rate = (
        sum(1 for item in results if item.get("retrieval_hit")) / len(results)
        if results else 0.0
    )
    accepted_rate = accepted / len(reviewed) if reviewed else 0.0
    consistency = _consistency(reviewed)
    policy_pass = (
        bool(reviewed)
        and not errors
        and consistency >= 0.80
        and critical_ambiguous == 0
        and accepted_rate >= 0.90
    )
    now = datetime.now(timezone.utc)
    report = {
        "schema_version": "rag-quality-live-sut.v1",
        "status": "live_quality_passed" if policy_pass else "live_quality_failed",
        "qualification_status": "sut_qualified" if policy_pass else "sut_not_qualified",
        "quality_comparable": bool(policy_pass and fixture.get("candidate_status") and lineage_match),
        "fixture": str(fixture_path),
        "fixture_hash": _hash(fixture_path.read_text(encoding="utf-8")),
        "snapshot_hash": fixture.get("snapshot", {}).get("manifest_hash"),
        "novel_id": NOVEL_ID,
        "retrieval": {"strategy": "formal_eval_service_bm25", "top_k": TOP_K},
        "answer_lineage": _answer_lineage(now),
        "judge_lineage": make_live_judge_lineage(now).model_dump(by_alias=True, mode="json"),
        "calibration_report": {
            "path": str(args.calibration),
            "status": calibration.status,
            "suite_hash": calibration.suite_hash,
            "lineage_match": lineage_match,
        },
        "policy": {
            "version": "rag-quality-policy.v1",
            "accepted_rate_min": 0.90,
            "critical_ambiguity_max": 0,
            "consistency_min": 0.80,
        },
        "metrics": {
            "cases": len(results),
            "reviewed": len(reviewed),
            "errors": len(errors),
            "retrieval_hit_rate_at_5": retrieval_hit_rate,
            "accepted_rate": accepted_rate,
            "verdict_consistency": consistency,
            "critical_ambiguity_count": critical_ambiguous,
            "classification_counts": dict(sorted(labels.items())),
            "p95_latency_ms": sorted((float(item.get("latency_ms", 0)) for item in results))[max(0, int(len(results) * 0.95) - 1)] if results else 0.0,
        },
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost,
            "cost_status": "estimated_from_price_snapshot",
            "pricing_snapshot": str(pricing_path),
        },
        "results": results,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default="evals/results/phase28/novel91-quality-candidate.json")
    parser.add_argument("--calibration", default="evals/results/phase28/novel91-live-calibration-report.json")
    parser.add_argument("--pricing-snapshot", default="evals/pricing/vertex-gemini-3.5-flash-lite-2026-07-28.json")
    parser.add_argument("--output", default="evals/results/phase28/novel91-live-rag-quality.json")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--max-cases", type=int, default=0)
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["metrics"], "estimated_cost_usd": report["usage"]["estimated_cost_usd"]}, ensure_ascii=False))
    print(f"[OK] wrote {output}")
    return 0 if report["status"] == "live_quality_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
