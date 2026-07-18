"""Bounded LLM semantic judgment for relationship evidence packages.

The model may only echo package IDs/enums. It never selects owner, version,
pipeline status, or persistence outcomes. Provider retries are 0; one same-
deployment schema repair is allowed and audited.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from app.schemas.relationship import RelationshipSemanticJudgment
from app.services.relationships.evidence import (
    RelationshipEvidencePackage,
    sha256_json,
    sha256_text,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "relationship_semantic_judge.v1.txt"
PROMPT_VERSION = "relationship_semantic_judge.v1"
SCHEMA_VERSION = "relationship-semantic-judgment.v1"
MAX_JUDGE_TOKENS = 1200
DECODING_SPEC = {"temperature": 0.0, "stream": False, "provider_retries": 0, "max_tokens": MAX_JUDGE_TOKENS}

ChatCallable = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class JudgmentCallResult:
    """Parsed judgment plus call audit fields (never an accepted observation)."""

    status: str
    gate_status: str
    structured: RelationshipSemanticJudgment | None
    structured_output: dict[str, Any] = field(default_factory=dict)
    raw_output_hash: str | None = None
    model_name: str = ""
    model_lineage: dict[str, Any] = field(default_factory=dict)
    prompt_hash: str = ""
    schema_hash: str = ""
    decoding_hash: str = ""
    gate_failures: list[str] = field(default_factory=list)
    call_skipped: bool = False
    cache_hit: bool = False
    repair_attempted: bool = False
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    rationale: str | None = None
    risk_flags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    relation_type: str = ""
    transition: str = "uncertain"
    valid_from_evidence_id: str = ""
    valid_to_evidence_id: str | None = None
    supporting_evidence_ids: list[str] = field(default_factory=list)


class RelationshipJudgmentService:
    """Call AIService (or injected chat fn) and validate strict structured output."""

    def __init__(
        self,
        *,
        chat_fn: ChatCallable | None = None,
        model_name: str | None = None,
        exact_cache: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._chat_fn = chat_fn
        self._model_name = model_name
        self._exact_cache = exact_cache if exact_cache is not None else {}
        self._prompt_text = self._load_prompt()
        self.prompt_hash = sha256_text(self._prompt_text)
        self.schema_hash = sha256_json(
            RelationshipSemanticJudgment.model_json_schema()
        )
        self.decoding_hash = sha256_json(DECODING_SPEC)

    def _load_prompt(self) -> str:
        if PROMPT_PATH.is_file():
            return PROMPT_PATH.read_text(encoding="utf-8")
        return (
            "Judge fiction character relationships using only the evidence package. "
            "Return JSON matching relationship-semantic-judgment.v1."
        )

    def resolve_model_name(self) -> str:
        if self._model_name:
            return self._model_name
        try:
            from app.services.ai_router import ai_router

            model = ai_router.route_task("extraction")
            prefix = f"{model.provider}/"
            if model.model_id.startswith(prefix):
                return model.model_id
            return f"{model.provider}/{model.model_id}"
        except Exception:
            return "test/relationship-judge"

    def cache_key_for(
        self,
        package: RelationshipEvidencePackage,
        *,
        model_name: str,
        policy_hash_value: str,
    ) -> str:
        return sha256_json(
            {
                "source_snapshot_hash": package.source_snapshot_hash,
                "hierarchy_checksum": package.hierarchy_checksum,
                "analysis_version_id": package.analysis_version_id,
                "source_judgment_checksum": package.source_judgment_checksum,
                "evidence_package_hash": package.package_hash,
                "prompt_hash": self.prompt_hash,
                "schema_hash": self.schema_hash,
                "model_name": model_name,
                "decoding_hash": self.decoding_hash,
                "policy_hash": policy_hash_value,
            }
        )

    async def judge_package(
        self,
        package: RelationshipEvidencePackage,
        *,
        policy_hash_value: str,
        deterministic_output: dict[str, Any] | None = None,
    ) -> JudgmentCallResult:
        """Judge one package. Network is never held under a DB lock by this method alone."""

        model_name = self.resolve_model_name()
        model_lineage = {
            "model_name": model_name,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "decoding": DECODING_SPEC,
        }

        # Deterministic completeness path (tests / fully-specified packages).
        if deterministic_output is not None:
            parsed = self.parse_and_validate(deterministic_output, package=package)
            parsed.call_skipped = True
            parsed.model_name = model_name
            parsed.model_lineage = {**model_lineage, "call_skipped_reason": "deterministic_output"}
            parsed.prompt_hash = self.prompt_hash
            parsed.schema_hash = self.schema_hash
            parsed.decoding_hash = self.decoding_hash
            return parsed

        cache_key = self.cache_key_for(
            package, model_name=model_name, policy_hash_value=policy_hash_value
        )
        cached = self._exact_cache.get(cache_key)
        if cached and cached.get("status") == "ok":
            parsed = self.parse_and_validate(cached["structured_output"], package=package)
            parsed.call_skipped = True
            parsed.cache_hit = True
            parsed.model_name = model_name
            parsed.model_lineage = {
                **model_lineage,
                "call_skipped_reason": "exact_cache",
                "cache_key": cache_key,
            }
            parsed.prompt_hash = self.prompt_hash
            parsed.schema_hash = self.schema_hash
            parsed.decoding_hash = self.decoding_hash
            return parsed

        started = time.perf_counter()
        messages = [
            {"role": "system", "content": self._prompt_text},
            {
                "role": "user",
                "content": json.dumps(package.to_llm_payload(), ensure_ascii=False),
            },
        ]

        try:
            response = await self._chat(messages, model_name=model_name)
        except Exception as exc:
            return JudgmentCallResult(
                status="rejected",
                gate_status="rejected",
                structured=None,
                structured_output={"error": str(exc), "error_type": type(exc).__name__},
                model_name=model_name,
                model_lineage=model_lineage,
                prompt_hash=self.prompt_hash,
                schema_hash=self.schema_hash,
                decoding_hash=self.decoding_hash,
                gate_failures=[f"provider_error:{type(exc).__name__}"],
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        latency_ms = (time.perf_counter() - started) * 1000
        content = _response_content(response)
        usage = _response_usage(response)
        raw_hash = sha256_text(content)

        parsed = self.parse_and_validate(
            content,
            package=package,
            raw_text=content,
        )
        parsed.model_name = model_name
        parsed.model_lineage = model_lineage
        parsed.prompt_hash = self.prompt_hash
        parsed.schema_hash = self.schema_hash
        parsed.decoding_hash = self.decoding_hash
        parsed.latency_ms = latency_ms
        parsed.prompt_tokens = usage.get("prompt_tokens")
        parsed.completion_tokens = usage.get("completion_tokens")
        parsed.raw_output_hash = raw_hash

        # One same-deployment schema repair only.
        if parsed.status in {"schema_failed", "evidence_failed"} and not parsed.repair_attempted:
            repair = await self._repair_once(
                package=package,
                model_name=model_name,
                model_lineage=model_lineage,
                previous_content=content,
                previous_failures=parsed.gate_failures,
            )
            if repair is not None:
                repair.repair_attempted = True
                repair.latency_ms = (repair.latency_ms or 0) + latency_ms
                parsed = repair

        if parsed.structured is not None and parsed.status == "pending":
            self._exact_cache[cache_key] = {
                "status": "ok",
                "structured_output": parsed.structured_output,
                "raw_output_hash": parsed.raw_output_hash,
            }

        return parsed

    async def _repair_once(
        self,
        *,
        package: RelationshipEvidencePackage,
        model_name: str,
        model_lineage: dict[str, Any],
        previous_content: str,
        previous_failures: list[str],
    ) -> JudgmentCallResult | None:
        repair_messages = [
            {"role": "system", "content": self._prompt_text},
            {
                "role": "user",
                "content": json.dumps(package.to_llm_payload(), ensure_ascii=False),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "repair": True,
                        "previous_failures": previous_failures,
                        "instruction": (
                            "Return only valid relationship-semantic-judgment.v1 JSON. "
                            "Use only package IDs and allowed enums."
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        started = time.perf_counter()
        try:
            response = await self._chat(repair_messages, model_name=model_name)
        except Exception as exc:
            return JudgmentCallResult(
                status="rejected",
                gate_status="rejected",
                structured=None,
                structured_output={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "repair": True,
                },
                model_name=model_name,
                model_lineage={**model_lineage, "repair": True},
                prompt_hash=self.prompt_hash,
                schema_hash=self.schema_hash,
                decoding_hash=self.decoding_hash,
                gate_failures=[f"repair_provider_error:{type(exc).__name__}"],
                repair_attempted=True,
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        content = _response_content(response)
        usage = _response_usage(response)
        parsed = self.parse_and_validate(content, package=package, raw_text=content)
        parsed.model_name = model_name
        parsed.model_lineage = {**model_lineage, "repair": True}
        parsed.prompt_hash = self.prompt_hash
        parsed.schema_hash = self.schema_hash
        parsed.decoding_hash = self.decoding_hash
        parsed.latency_ms = (time.perf_counter() - started) * 1000
        parsed.prompt_tokens = usage.get("prompt_tokens")
        parsed.completion_tokens = usage.get("completion_tokens")
        parsed.raw_output_hash = sha256_text(content)
        parsed.repair_attempted = True
        return parsed

    def parse_and_validate(
        self,
        content: str | dict[str, Any],
        *,
        package: RelationshipEvidencePackage,
        raw_text: str | None = None,
    ) -> JudgmentCallResult:
        """Parse model output without accepting graph facts."""

        try:
            if isinstance(content, dict):
                data = content
            else:
                data = _loads_model_json(content)
            output = RelationshipSemanticJudgment.model_validate(data)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            return JudgmentCallResult(
                status="schema_failed",
                gate_status="schema_failed",
                structured=None,
                structured_output={"content": raw_text or content, "error": str(exc)},
                raw_output_hash=sha256_text(raw_text or str(content)),
                gate_failures=[f"schema:{type(exc).__name__}"],
                prompt_hash=self.prompt_hash,
                schema_hash=self.schema_hash,
                decoding_hash=self.decoding_hash,
            )

        failures: list[str] = []
        if output.candidate_key != package.candidate_key:
            failures.append("candidate_key_mismatch")
        if output.source_ref != package.source_ref:
            failures.append("source_ref_mismatch")
        if output.target_ref != package.target_ref:
            failures.append("target_ref_mismatch")

        allowed = set(package.allowed_evidence_ids())
        cited = list(output.supporting_evidence_ids) + [output.valid_from_evidence_id]
        if output.valid_to_evidence_id:
            cited.append(output.valid_to_evidence_id)
        out_of_package = sorted({eid for eid in cited if eid not in allowed})
        if out_of_package:
            failures.append(f"out_of_package_evidence:{','.join(out_of_package)}")

        rel = (
            output.relation_type.value
            if hasattr(output.relation_type, "value")
            else str(output.relation_type)
        )
        if rel not in package.allowed_relation_types:
            failures.append(f"relation_type_not_allowed:{rel}")

        structured = output.model_dump(mode="json")
        if failures:
            status = "evidence_failed" if out_of_package else "schema_failed"
            gate_status = status
        else:
            status = "pending"
            gate_status = "schema_passed"

        transition = (
            output.transition.value
            if hasattr(output.transition, "value")
            else str(output.transition)
        )
        return JudgmentCallResult(
            status=status,
            gate_status=gate_status,
            structured=output if not failures else None,
            structured_output=structured,
            raw_output_hash=sha256_text(raw_text or json.dumps(structured, ensure_ascii=False)),
            gate_failures=failures,
            prompt_hash=self.prompt_hash,
            schema_hash=self.schema_hash,
            decoding_hash=self.decoding_hash,
            rationale=output.rationale,
            risk_flags=list(output.risk_flags or []),
            confidence=float(output.confidence),
            relation_type=rel,
            transition=transition,
            valid_from_evidence_id=output.valid_from_evidence_id,
            valid_to_evidence_id=output.valid_to_evidence_id,
            supporting_evidence_ids=list(output.supporting_evidence_ids),
        )

    async def _chat(self, messages: list[dict[str, str]], *, model_name: str) -> Any:
        if self._chat_fn is not None:
            return await self._chat_fn(
                messages=messages,
                model=model_name,
                temperature=0.0,
                max_tokens=MAX_JUDGE_TOKENS,
            )
        from app.services.ai_service import ai_service

        return await ai_service.chat(
            messages=messages,
            model=model_name,
            temperature=0.0,
            max_tokens=MAX_JUDGE_TOKENS,
        )


def _loads_model_json(content: str) -> dict[str, Any]:
    stripped = (content or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise TypeError("LLM judgment output must be a JSON object")
    return data


def _response_content(response: Any) -> str:
    if isinstance(response, dict):
        if "content" in response:
            return str(response.get("content") or "")
        choices = response.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            return str(message.get("content") or "")
        return ""
    try:
        return response.choices[0].message.content or ""
    except Exception:
        return str(response or "")


def _response_usage(response: Any) -> dict[str, int | None]:
    usage = None
    if isinstance(response, dict):
        usage = response.get("usage")
    else:
        usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": None, "completion_tokens": None}

    def _get(name: str) -> int | None:
        if isinstance(usage, dict):
            value = usage.get(name)
            if value is None and name == "prompt_tokens":
                value = usage.get("input_tokens")
            if value is None and name == "completion_tokens":
                value = usage.get("output_tokens")
        else:
            value = getattr(usage, name, None)
        return int(value) if value is not None else None

    return {
        "prompt_tokens": _get("prompt_tokens"),
        "completion_tokens": _get("completion_tokens"),
    }


relationship_judgment_service = RelationshipJudgmentService()
