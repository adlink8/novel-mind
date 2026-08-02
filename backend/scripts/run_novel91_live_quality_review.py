#!/usr/bin/env python3
"""Run an independent live semantic review over the novel-91 candidate fixture.

This is an evidence-producing review, not a production qualification command.
It deliberately records rejected or malformed judgments and keeps cost status
unknown because the generic AI usage logger does not contain a price snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.eval import ModelLineage
from app.services.ai_service import _extract_token_usage, ai_service
from app.services.rag_fixture import schema_contract_hash, stable_hash

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def make_live_judge_lineage(started_at: datetime) -> ModelLineage:
    """Return the lineage shared by live review, calibration, and candidate gold."""
    return ModelLineage(
        provider="vertex_google",
        model_family=LIVE_JUDGE_MODEL_FAMILY,
        model_id=DEFAULT_MODEL,
        **{"weights/revision": LIVE_JUDGE_WEIGHTS_REVISION},
        endpoint_class="live_external_review",
        prompt_hash=LIVE_JUDGE_PROMPT_HASH,
        prompt_version=LIVE_JUDGE_PROMPT_VERSION,
        schema_hash=schema_contract_hash(),
        runtime="vertex_live",
        started_at=started_at,
    )


DEFAULT_MODEL = "vertex_google/gemini-3.5-flash-lite"
DEFAULT_PRICING = Path("evals/pricing/vertex-gemini-3.5-flash-lite-2026-07-28.json")
LIVE_JUDGE_PROMPT_VERSION = "novel91.live.semantic-review.v2"
LIVE_JUDGE_WEIGHTS_REVISION = "vertex-gemini-3.5-flash-lite-2026-07-28"
LIVE_JUDGE_MODEL_FAMILY = "vertex_gemini_live_judge"
LIVE_JUDGE_POLICY_TEXT = """你是独立的小说检索质量审查员。只根据问题、候选答案和证据判断证据关系，不能因为候选答案存在就默认接受。
分类规则：accept=证据直接且完整支持候选答案；partial=只支持部分答案；reject=证据不足或问题不可回答；contradictory=证据与候选答案冲突；no_answer=候选答案明确表示不知道且证据不足；hard_negative=表面相似但不能支持候选答案。
输出必须是一个 JSON 对象，classification 只能是 accept、partial、reject、contradictory、no_answer、hard_negative 之一。"""
LIVE_JUDGE_PROMPT_HASH = stable_hash(
    {"prompt_version": LIVE_JUDGE_PROMPT_VERSION, "policy": LIVE_JUDGE_POLICY_TEXT}
)
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_prompt(case: dict[str, Any]) -> str:
    refs: list[str] = []
    for evidence_set in case.get("equivalent_evidence_sets", []):
        for ref in evidence_set.get("refs", []):
            quote = str(ref.get("quote_text") or "").strip()
            if quote and quote not in refs:
                refs.append(quote)
    evidence = "\n---\n".join(refs) or "（无证据）"
    return f"""{LIVE_JUDGE_POLICY_TEXT}

问题：{case.get('question', '')}
参考答案：{case.get('reference_answer', '')}
证据原文：
{evidence}

审查规则：
1. 证据必须实际包含或明确蕴含参考答案；只有问题片段、残句或无法独立回答的问题必须拒绝。
2. coverage 和 sufficiency 反映证据是否完整回答问题，而不是文字是否相似。
3. 只输出一个 JSON 对象，不要 Markdown，不要解释。字段必须是：
{{"classification":"accept|partial|reject|contradictory|no_answer|hard_negative","accepted":true或false,"faithfulness":1到4,"coverage":1到4,"sufficiency":1到4,"critical_ambiguity":0或1,"reason_codes":["UPPER_SNAKE_CASE"]}}
"""


def _parse_verdict(text: str) -> dict[str, Any]:
    match = JSON_BLOCK_RE.search(text or "")
    if not match:
        raise ValueError("no_json_object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("json_not_object")
    classification = value.get("classification")
    if classification not in {
        "accept",
        "partial",
        "reject",
        "contradictory",
        "no_answer",
        "hard_negative",
    }:
        raise ValueError("classification_invalid")
    accepted = value.get("accepted")
    if not isinstance(accepted, bool):
        raise ValueError("accepted_not_bool")
    for field in ("faithfulness", "coverage", "sufficiency"):
        score = value.get(field)
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 4:
            raise ValueError(f"{field}_out_of_range")
    ambiguity = value.get("critical_ambiguity")
    if ambiguity not in (0, 1):
        raise ValueError("critical_ambiguity_invalid")
    reasons = value.get("reason_codes")
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise ValueError("reason_codes_invalid")
    return {
        "classification": classification,
        "accepted": accepted,
        "faithfulness": value["faithfulness"],
        "coverage": value["coverage"],
        "sufficiency": value["sufficiency"],
        "critical_ambiguity": ambiguity,
        "reason_codes": reasons,
    }


async def _review_one(
    case: dict[str, Any],
    *,
    model: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    prompt = _review_prompt(case)
    async with semaphore:
        try:
            response = await ai_service.chat(
                [
                    {
                        "role": "system",
                        "content": "你必须严格独立审查，不能把候选生成器的判断当作事实。",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=0,
                max_tokens=220,
                task_type="rag_quality_live_judge",
            )
            text = response.choices[0].message.content or ""
            verdict = _parse_verdict(text)
            input_tokens, output_tokens = _extract_token_usage(response)
            return {
                "case_id": case.get("case_id"),
                "status": "reviewed",
                "verdict": verdict,
                "response_hash": _hash_text(text),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
        except Exception as exc:  # noqa: BLE001 - preserve per-case audit evidence
            return {
                "case_id": case.get("case_id"),
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:240],
            }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    pricing_path = Path(args.pricing_snapshot)
    pricing = json.loads(pricing_path.read_text(encoding="utf-8")) if pricing_path.exists() else None
    cases = fixture.get("cases") or []
    if args.max_cases:
        cases = cases[: args.max_cases]
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    results = await asyncio.gather(
        *[
            _review_one(case, model=args.model, semaphore=semaphore)
            for case in cases
        ]
    )
    accepted = [
        item
        for item in results
        if item.get("status") == "reviewed" and item.get("verdict", {}).get("accepted") is True
    ]
    rejected = [
        item
        for item in results
        if item.get("status") == "reviewed" and item.get("verdict", {}).get("accepted") is False
    ]
    errors = [item for item in results if item.get("status") == "error"]
    reason_counts: dict[str, int] = {}
    for item in results:
        for reason in item.get("verdict", {}).get("reason_codes", []):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    input_tokens = sum(int(item.get("input_tokens", 0)) for item in results)
    output_tokens = sum(int(item.get("output_tokens", 0)) for item in results)
    estimated_cost = None
    cost_status = "unknown_no_price_snapshot"
    if pricing:
        estimated_cost = (
            input_tokens * float(pricing["input_usd_per_1m_tokens"])
            + output_tokens * float(pricing["output_usd_per_1m_tokens"])
        ) / 1_000_000
        cost_status = "estimated_from_price_snapshot"
    review_passed = bool(results) and not errors and not rejected
    report = {
        "schema_version": "rag-quality-live-review.v1",
        "status": "semantic_review_passed" if review_passed else "semantic_review_failed",
        "qualification_status": "not_qualified",
        "quality_comparable": False,
        "fixture": str(args.fixture),
        "fixture_hash": _hash_text(Path(args.fixture).read_text(encoding="utf-8")),
        "snapshot_hash": fixture.get("snapshot", {}).get("manifest_hash"),
        "candidate_status": fixture.get("candidate_status"),
        "judge_lineage": make_live_judge_lineage(
            datetime.now(timezone.utc)
        ).model_dump(by_alias=True, mode="json")
        | {"decoding": {"temperature": 0, "max_tokens": 220}},
        "started_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "cases": len(results),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "errors": len(errors),
            "accepted_rate": (len(accepted) / len(results)) if results else 0.0,
        },
        "reason_counts": dict(sorted(reason_counts.items())),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd_recorded": 0.0,
            "estimated_cost_usd": estimated_cost,
            "cost_status": cost_status,
            "pricing_snapshot": str(pricing_path) if pricing else None,
        },
        "results": results,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default="evals/results/phase28/novel91-quality-candidate.json")
    parser.add_argument("--output", default="evals/results/phase28/novel91-live-review.json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--pricing-snapshot", default=str(DEFAULT_PRICING))
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--max-cases", type=int, default=0)
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["counts"], "reason_counts": report["reason_counts"]}, ensure_ascii=False))
    print(f"[OK] wrote {output}")
    return 0 if report["status"] == "semantic_review_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
