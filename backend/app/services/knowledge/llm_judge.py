"""Structured LLM judgment for evidence-bounded relation candidates."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import (
    RELATION_TYPES_BY_DOMAIN_PROFILE,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.schemas.knowledge import KnowledgeLLMRelationJudgmentOutput
from app.services.ai_router import ai_router
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)

PROMPT_VERSION = "knowledge-relation-judge.v1"
MAX_JUDGE_TOKENS = 1200

SYSTEM_PROMPT = """You are a knowledge graph relation judge.
Use only the evidence package supplied by the user.
Do not infer unsupported facts.
Recall signals such as vector, BM25, adjacency, entity overlap, or time-window
are only retrieval hints and are not evidence of truth.
Return JSON only with exactly these keys:
candidate_id, relation_type, confidence, evidence_refs, rationale,
risk_flags, needs_human_review.
Every evidence_refs item must be one of the package allowed_evidence_ids.
If evidence is weak, set needs_human_review=true and explain the risk."""


@dataclass(slots=True)
class JudgmentResult:
    """Parsed or failed LLM judgment result."""

    status: str
    gate_status: str
    candidate_id: int
    model_name: str
    prompt_version: str = PROMPT_VERSION
    relation_type: str = ""
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    rationale: str | None = None
    risk_flags: list[str] = field(default_factory=list)
    needs_human_review: bool = False
    raw_output: dict[str, Any] = field(default_factory=dict)
    structured_output: dict[str, Any] = field(default_factory=dict)
    gate_failures: list[str] = field(default_factory=list)
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None


class KnowledgeLLMJudgeService:
    """Call the approved AI service and validate structured relation judgments."""

    async def judge_package(
        self,
        package: dict[str, Any],
        *,
        persist: bool = False,
        db: AsyncSession | None = None,
        candidate: KnowledgeRelationCandidate | None = None,
    ) -> JudgmentResult:
        """Judge one evidence package and optionally persist the audit row."""

        model_name = self._resolve_model_name()
        candidate_id = int(package["candidate"]["candidate_id"])
        started = time.perf_counter()

        try:
            response = await ai_service.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(package, ensure_ascii=False)},
                ],
                model=model_name,
                temperature=0.1,
                max_tokens=MAX_JUDGE_TOKENS,
            )
        except Exception as exc:
            result = JudgmentResult(
                status="blocked",
                gate_status="rejected",
                candidate_id=candidate_id,
                model_name=model_name,
                relation_type=package["candidate"].get("relation_type", ""),
                evidence_refs=list(package.get("allowed_evidence_ids", [])),
                raw_output={"error": str(exc), "error_type": type(exc).__name__},
                gate_failures=[f"blocked:{type(exc).__name__}"],
                latency_ms=(time.perf_counter() - started) * 1000,
                needs_human_review=True,
            )
            await self._persist_if_requested(result, persist=persist, db=db, candidate=candidate)
            return result

        latency_ms = (time.perf_counter() - started) * 1000
        content = _response_content(response)
        usage = _response_usage(response)
        raw_output = {"content": content, "usage": usage}
        parsed = self.parse_judgment(
            content,
            package=package,
            model_name=model_name,
            latency_ms=latency_ms,
            raw_output=raw_output,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
        await self._persist_if_requested(parsed, persist=persist, db=db, candidate=candidate)
        return parsed

    def parse_judgment(
        self,
        content: str,
        *,
        package: dict[str, Any],
        model_name: str,
        latency_ms: float | None = None,
        raw_output: dict[str, Any] | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> JudgmentResult:
        """Parse and gate model output without accepting any graph fact."""

        candidate_id = int(package["candidate"]["candidate_id"])
        fallback_relation_type = package["candidate"].get("relation_type", "")
        allowed_evidence_ids = set(package.get("allowed_evidence_ids", []))
        allowed_relation_types = set(
            package.get("allowed_relation_types")
            or RELATION_TYPES_BY_DOMAIN_PROFILE.get(package.get("domain_profile"), ())
        )

        try:
            parsed_json = _loads_model_json(content)
            output = KnowledgeLLMRelationJudgmentOutput.model_validate(parsed_json)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            return JudgmentResult(
                status="schema_failed",
                gate_status="schema_failed",
                candidate_id=candidate_id,
                model_name=model_name,
                relation_type=fallback_relation_type,
                evidence_refs=list(allowed_evidence_ids),
                raw_output=raw_output or {"content": content},
                gate_failures=[f"schema:{type(exc).__name__}"],
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                needs_human_review=True,
            )

        structured = output.model_dump()
        gate_failures: list[str] = []
        if output.candidate_id != candidate_id:
            gate_failures.append("candidate_id_mismatch")
        if output.relation_type not in allowed_relation_types:
            gate_failures.append("relation_type_not_allowed")
        out_of_package = sorted(set(output.evidence_refs) - allowed_evidence_ids)
        if out_of_package:
            gate_failures.append(f"out_of_package_evidence:{','.join(out_of_package)}")

        if gate_failures:
            status = "evidence_failed" if out_of_package else "schema_failed"
            gate_status = "evidence_failed" if out_of_package else "schema_failed"
            needs_review = True
        else:
            status = "needs_human_review" if output.needs_human_review else "pending"
            gate_status = "evidence_passed"
            needs_review = output.needs_human_review

        return JudgmentResult(
            status=status,
            gate_status=gate_status,
            candidate_id=output.candidate_id,
            model_name=model_name,
            relation_type=output.relation_type,
            confidence=output.confidence,
            evidence_refs=output.evidence_refs,
            rationale=output.rationale,
            risk_flags=output.risk_flags,
            needs_human_review=needs_review,
            raw_output=raw_output or {"content": content},
            structured_output=structured,
            gate_failures=gate_failures,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )

    async def persist_judgment_result(
        self,
        db: AsyncSession,
        *,
        candidate: KnowledgeRelationCandidate,
        result: JudgmentResult,
    ) -> KnowledgeRelationJudgment:
        """Persist judgment audit data without marking an accepted graph fact."""

        judgment = KnowledgeRelationJudgment(
            owner_id=candidate.owner_id,
            novel_id=candidate.novel_id,
            run_id=candidate.run_id,
            relation_candidate_id=candidate.id,
            prompt_version=result.prompt_version,
            model_name=result.model_name,
            relation_type=result.relation_type or candidate.relation_type,
            confidence=result.confidence,
            evidence_refs=result.evidence_refs or candidate.evidence_refs,
            rationale=result.rationale,
            risk_flags=result.risk_flags,
            raw_output=result.raw_output,
            structured_output=result.structured_output,
            status=result.status,
            gate_status=result.gate_status,
            gate_failures=result.gate_failures,
            needs_human_review=result.needs_human_review,
            latency_ms=result.latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
        )
        db.add(judgment)
        candidate.status = "needs_human_review" if result.needs_human_review else "proposed"
        await db.flush()
        return judgment

    async def _persist_if_requested(
        self,
        result: JudgmentResult,
        *,
        persist: bool,
        db: AsyncSession | None,
        candidate: KnowledgeRelationCandidate | None,
    ) -> None:
        if not persist:
            return
        if db is None or candidate is None:
            raise ValueError("db and candidate are required when persist=True")
        await self.persist_judgment_result(db, candidate=candidate, result=result)

    def _resolve_model_name(self) -> str:
        model = ai_router.route_task("extraction")
        prefix = f"{model.provider}/"
        if model.model_id.startswith(prefix):
            return model.model_id
        return f"{model.provider}/{model.model_id}"


def _loads_model_json(content: str) -> dict[str, Any]:
    stripped = (content or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise TypeError("LLM judgment output must be a JSON object")
    return data


def _response_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except Exception:
        content = None
    return content or ""


def _response_usage(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {"prompt_tokens": None, "completion_tokens": None}

    def _get(name: str) -> int | None:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        return int(value) if value is not None else None

    return {
        "prompt_tokens": _get("prompt_tokens"),
        "completion_tokens": _get("completion_tokens"),
    }


llm_judge_service = KnowledgeLLMJudgeService()
