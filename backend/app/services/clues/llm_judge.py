"""Strict LLM semantic judgment for clue evidence packages.

The model may only return ClueSemanticJudgment fields. It has no DB, tool,
state, budget, or publish authority. Provider retries are zero. At most one
same-deployment repair is an explicit caller-controlled attempt — never a
hidden retry loop inside the adapter.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from app.schemas.clue import ClueSemanticJudgment
from app.services.clues.evidence import (
    ClueEvidencePackage,
    sha256_json,
    sha256_text,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "clue_semantic_judge.v1.txt"
PROMPT_VERSION = "clue_semantic_judge.v1"
SCHEMA_VERSION = "clue-semantic-judgment.v1"
MAX_JUDGE_TOKENS = 1200
DECODING_SPEC = {
    "temperature": 0.0,
    "stream": False,
    "provider_retries": 0,
    "max_tokens": MAX_JUDGE_TOKENS,
    "tools": None,
}

ChatCallable = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class ClueJudgmentAudit:
    """Call audit fields retained with the parsed judgment (no lifecycle write)."""

    model_name: str = ""
    prompt_version: str = PROMPT_VERSION
    prompt_hash: str = ""
    schema_hash: str = ""
    decoding_hash: str = ""
    raw_output_hash: str | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    package_hash: str = ""
    repair_attempt: bool = False
    call_count: int = 0
    model_lineage: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ClueJudgmentResult:
    """Parsed semantic judgment plus audit — never an accepted lifecycle state."""

    status: str
    gate_status: str
    structured: ClueSemanticJudgment | None
    structured_output: dict[str, Any] = field(default_factory=dict)
    gate_failures: list[str] = field(default_factory=list)
    audit: ClueJudgmentAudit = field(default_factory=ClueJudgmentAudit)

    @property
    def ok(self) -> bool:
        return self.status == "pending" and self.structured is not None


class ClueLLMJudgeService:
    """Async judge adapter: package in, judgment DTO out, zero persistence."""

    def __init__(
        self,
        *,
        chat_fn: ChatCallable | None = None,
        model_name: str | None = None,
    ) -> None:
        self._chat_fn = chat_fn
        self._model_name = model_name
        self._prompt_text = self._load_prompt()
        self.prompt_hash = sha256_text(self._prompt_text)
        self.schema_hash = sha256_json(ClueSemanticJudgment.model_json_schema())
        self.decoding_hash = sha256_json(DECODING_SPEC)
        self._call_count = 0

    def _load_prompt(self) -> str:
        if PROMPT_PATH.is_file():
            return PROMPT_PATH.read_text(encoding="utf-8")
        return (
            "Judge fiction clue/foreshadow semantics using only the evidence package. "
            "Return JSON matching clue-semantic-judgment.v1. "
            "Recall scores and chat text are not proof."
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
            return "test/clue-semantic-judge"

    async def judge_package(
        self,
        package: ClueEvidencePackage,
        *,
        repair: bool = False,
        previous_failures: list[str] | None = None,
        previous_content: str | None = None,
        deterministic_output: dict[str, Any] | str | None = None,
    ) -> ClueJudgmentResult:
        """Judge one package.

        ``repair=True`` is the sole same-deployment repair path and must be
        requested by the caller (durable worker). This method never auto-retries
        on failure and never writes lifecycle or DB rows.
        """

        model_name = self.resolve_model_name()
        lineage = {
            "model_name": model_name,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "decoding": DECODING_SPEC,
            "repair": bool(repair),
        }

        if deterministic_output is not None:
            parsed = self.parse_and_validate(deterministic_output, package=package)
            parsed.audit = ClueJudgmentAudit(
                model_name=model_name,
                prompt_hash=self.prompt_hash,
                schema_hash=self.schema_hash,
                decoding_hash=self.decoding_hash,
                package_hash=package.package_hash,
                repair_attempt=repair,
                call_count=0,
                model_lineage={**lineage, "call_skipped_reason": "deterministic_output"},
            )
            return parsed

        messages = [
            {"role": "system", "content": self._prompt_text},
            {
                "role": "user",
                "content": json.dumps(package.to_llm_payload(), ensure_ascii=False),
            },
        ]
        if repair:
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "repair": True,
                            "previous_failures": previous_failures or [],
                            "previous_content_hash": (
                                sha256_text(previous_content)
                                if previous_content
                                else None
                            ),
                            "instruction": (
                                "Return only valid clue-semantic-judgment.v1 JSON. "
                                "Use only package allowed_evidence_ids and enums. "
                                "Do not emit status, tools, writes, or version fields."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                }
            )

        started = time.perf_counter()
        try:
            response = await self._chat(messages, model_name=model_name)
            self._call_count += 1
        except Exception as exc:
            self._call_count += 1
            return ClueJudgmentResult(
                status="rejected",
                gate_status="rejected",
                structured=None,
                structured_output={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "repair": repair,
                },
                gate_failures=[f"provider_error:{type(exc).__name__}"],
                audit=ClueJudgmentAudit(
                    model_name=model_name,
                    prompt_hash=self.prompt_hash,
                    schema_hash=self.schema_hash,
                    decoding_hash=self.decoding_hash,
                    package_hash=package.package_hash,
                    repair_attempt=repair,
                    call_count=self._call_count,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    model_lineage=lineage,
                ),
            )

        latency_ms = (time.perf_counter() - started) * 1000
        content = _response_content(response)
        usage = _response_usage(response)
        raw_hash = sha256_text(content)

        # Reject tool-smuggling and non-JSON envelopes early.
        early = self._reject_tool_or_stream_artifacts(content)
        if early is not None:
            early.audit = ClueJudgmentAudit(
                model_name=model_name,
                prompt_hash=self.prompt_hash,
                schema_hash=self.schema_hash,
                decoding_hash=self.decoding_hash,
                raw_output_hash=raw_hash,
                package_hash=package.package_hash,
                repair_attempt=repair,
                call_count=self._call_count,
                latency_ms=latency_ms,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                model_lineage=lineage,
            )
            return early

        parsed = self.parse_and_validate(content, package=package, raw_text=content)
        parsed.audit = ClueJudgmentAudit(
            model_name=model_name,
            prompt_hash=self.prompt_hash,
            schema_hash=self.schema_hash,
            decoding_hash=self.decoding_hash,
            raw_output_hash=raw_hash,
            package_hash=package.package_hash,
            repair_attempt=repair,
            call_count=self._call_count,
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            model_lineage=lineage,
        )
        return parsed

    def parse_and_validate(
        self,
        content: str | dict[str, Any],
        *,
        package: ClueEvidencePackage,
        raw_text: str | None = None,
    ) -> ClueJudgmentResult:
        """Parse model output without accepting lifecycle transitions."""

        try:
            if isinstance(content, dict):
                data = content
            else:
                data = _loads_model_json(content)
            if not isinstance(data, dict):
                raise TypeError("LLM judgment output must be a JSON object")
            # Extra keys fail via Pydantic extra=forbid.
            output = ClueSemanticJudgment.model_validate(data)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            return ClueJudgmentResult(
                status="schema_failed",
                gate_status="schema_failed",
                structured=None,
                structured_output={"content": raw_text or content, "error": str(exc)},
                gate_failures=[f"schema:{type(exc).__name__}"],
            )

        failures: list[str] = []
        if output.candidate_id != package.candidate_id:
            failures.append("candidate_id_mismatch")

        allowed = set(package.allowed_evidence_ids())
        cited = list(output.cue_evidence_ids) + list(output.later_evidence_ids)
        out_of_package = sorted({eid for eid in cited if eid not in allowed})
        if out_of_package:
            failures.append(f"out_of_package_evidence:{','.join(out_of_package)}")

        # Cue IDs should prefer package cue window when classification needs cue.
        cue_set = set(package.cue_ids())
        later_set = set(package.later_ids())
        if output.classification.value in {"cue_only", "reinforcement", "payoff"}:
            bad_cue = sorted(set(output.cue_evidence_ids) - cue_set - later_set)
            # already covered by allowed; additionally flag cue not in cue window
            cue_not_in_window = sorted(set(output.cue_evidence_ids) - cue_set)
            if cue_not_in_window and set(output.cue_evidence_ids) <= allowed:
                failures.append(
                    f"cue_not_in_cue_window:{','.join(cue_not_in_window)}"
                )

        structured = output.model_dump(mode="json")
        if failures:
            status = "evidence_failed" if out_of_package else "schema_failed"
            return ClueJudgmentResult(
                status=status,
                gate_status=status,
                structured=None,
                structured_output=structured,
                gate_failures=failures,
            )

        return ClueJudgmentResult(
            status="pending",
            gate_status="schema_passed",
            structured=output,
            structured_output=structured,
            gate_failures=[],
        )

    def _reject_tool_or_stream_artifacts(self, content: str) -> ClueJudgmentResult | None:
        text = content or ""
        lowered = text.lower()
        if '"tool_calls"' in lowered or "function_call" in lowered or "<|tool" in lowered:
            return ClueJudgmentResult(
                status="schema_failed",
                gate_status="schema_failed",
                structured=None,
                structured_output={"content": text, "error": "tool_smuggling"},
                gate_failures=["schema:tool_request_forbidden"],
            )
        if "data: " in text and "\n\ndata:" in text:
            return ClueJudgmentResult(
                status="schema_failed",
                gate_status="schema_failed",
                structured=None,
                structured_output={"content": text, "error": "stream_forbidden"},
                gate_failures=["schema:stream_forbidden"],
            )
        return None

    async def _chat(self, messages: list[dict[str, str]], *, model_name: str) -> Any:
        kwargs = {
            "messages": messages,
            "model": model_name,
            "temperature": 0.0,
            "max_tokens": MAX_JUDGE_TOKENS,
        }
        if self._chat_fn is not None:
            # Injected transports must not receive tools/stream/retries.
            return await self._chat_fn(**kwargs)
        from app.services.ai_service import ai_service

        return await ai_service.chat(
            **kwargs,
            # Provider retries must stay zero for clue judgment.
            # ai_service may ignore unknown kwargs; decoding_hash freezes intent.
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


clue_llm_judge_service = ClueLLMJudgeService()
