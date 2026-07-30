"""Strict evidence-only reader-chat model gateway with dual budgets and one repair."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Awaitable, Callable, Protocol

from pydantic import ValidationError

from app.schemas.reader_chat import (
    ReaderAnswerEnvelope,
    validate_answer_against_manifest,
)
from app.services.reader_chat.budget import (
    BudgetExceeded,
    DualBudgetGate,
    DualBudgetRepository,
    PersistentDualAttempt,
    UnknownPricing,
)


class DependencyPaused(RuntimeError):
    """Resolved deployment cannot safely execute this structured stage."""


class ModelCallFailed(RuntimeError):
    def __init__(self, message: str, attempts: list["GatewayAttempt"]) -> None:
        super().__init__(message)
        self.attempts = attempts


class StructuredOutputRejected(ModelCallFailed):
    """Primary and sole repair both failed local schema/citation gates."""


class ModelTransport(Protocol):
    async def complete(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class ModelDeployment:
    provider: str
    model_id: str
    revision: str
    supports_structured_output: bool
    input_price_per_million: Decimal | None
    output_price_per_million: Decimal | None

    @property
    def resolved_name(self) -> str:
        return f"{self.provider}/{self.model_id}"

    @property
    def lineage(self) -> tuple[str, str, str]:
        return self.provider, self.model_id, self.revision

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "revision": self.revision,
            "supports_structured_output": self.supports_structured_output,
        }

    def price_snapshot(self) -> dict[str, Any]:
        return {
            "input_price_per_million": (
                str(self.input_price_per_million)
                if self.input_price_per_million is not None
                else None
            ),
            "output_price_per_million": (
                str(self.output_price_per_million)
                if self.output_price_per_million is not None
                else None
            ),
        }


@dataclass(frozen=True)
class GatewayAttempt:
    attempt_number: int
    status: str
    reservation_key: str
    request_hash: str
    response_hash: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: Decimal | None = None
    error_code: str | None = None
    latency_ms: int | None = None
    envelope: dict[str, Any] | None = None


@dataclass(frozen=True)
class GatewayResult:
    output: ReaderAnswerEnvelope
    attempts: list[GatewayAttempt]
    deployment: ModelDeployment
    response_hash: str


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _response_content(response: Any) -> str:
    if isinstance(response, dict) and isinstance(response.get("content"), str):
        text = response["content"]
    else:
        content = getattr(response, "content", None)
        if not isinstance(content, str):
            raise ValueError("provider response has no textual structured content")
        text = content
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```JSON").removeprefix("```")
        text = text.removesuffix("```").strip()
    return text


def _response_tool_calls(response: Any) -> list[dict[str, Any]]:
    raw_calls = (
        response.get("tool_calls", [])
        if isinstance(response, dict)
        else getattr(response, "tool_calls", [])
    ) or []
    normalized: list[dict[str, Any]] = []
    for index, call in enumerate(raw_calls):
        if isinstance(call, dict):
            function = call.get("function") or {}
            name = function.get("name") or call.get("name")
            arguments = function.get("arguments", call.get("arguments", {}))
            call_id = call.get("id") or f"tool-call-{index}"
        else:
            function = getattr(call, "function", None)
            name = getattr(function, "name", None) or getattr(call, "name", None)
            arguments = getattr(function, "arguments", None) or getattr(
                call, "arguments", {}
            )
            call_id = getattr(call, "id", None) or f"tool-call-{index}"
        if not name:
            continue
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        normalized.append(
            {
                "id": str(call_id),
                "type": "function",
                "function": {
                    "name": str(name),
                    "arguments": arguments if isinstance(arguments, dict) else {},
                },
            }
        )
    return normalized


def _response_usage(response: Any) -> dict[str, int]:
    raw = (
        response.get("usage", {})
        if isinstance(response, dict)
        else getattr(response, "usage", {})
    )
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        # Vertex returns SimpleNamespace(usage=SimpleNamespace(...))
        raw = {
            "prompt_tokens": getattr(raw, "prompt_tokens", None),
            "completion_tokens": getattr(raw, "completion_tokens", None),
            "input_tokens": getattr(raw, "input_tokens", None),
            "output_tokens": getattr(raw, "output_tokens", None),
        }
    return {
        "input_tokens": int(raw.get("input_tokens", raw.get("prompt_tokens", 0)) or 0),
        "output_tokens": int(
            raw.get("output_tokens", raw.get("completion_tokens", 0)) or 0
        ),
    }


def _response_request_id(response: Any) -> str | None:
    if isinstance(response, dict):
        value = response.get("id")
    else:
        value = getattr(response, "id", None)
    return str(value) if value is not None else None


def business_validate_answer(
    envelope: ReaderAnswerEnvelope,
    *,
    allowed_evidence_ids: set[str],
) -> None:
    """Citation membership + no-evidence + display-only suggestion gates."""

    validate_answer_against_manifest(envelope, allowed_evidence_ids)
    if not allowed_evidence_ids and envelope.answer_blocks:
        raise ValueError("no usable evidence: factual answer blocks are forbidden")
    for suggestion in envelope.suggestion_candidates:
        if suggestion.requires_explicit_confirmation is not True:
            raise ValueError("suggestions must require explicit confirmation")
        # Display-only: reject language that claims domain mutation already applied.
        lowered = suggestion.proposal.lower()
        for banned in (
            "already applied",
            "written to timeline",
            "updated relationship",
            "created clue",
            "already applied to domain",
        ):
            if banned in lowered:
                raise ValueError("suggestion claims domain write; rejected")


class ReaderChatGateway:
    """Owns reader-chat structured calls; worker owns lease/cancel/publication."""

    def __init__(
        self,
        transport: ModelTransport,
        *,
        persistence: DualBudgetRepository | None = None,
    ) -> None:
        self.transport = transport
        self.persistence = persistence

    async def generate(
        self,
        *,
        deployment: ModelDeployment,
        messages: list[dict[str, Any]],
        allowed_evidence_ids: set[str],
        budget: DualBudgetGate,
        job_id: int,
        max_input_tokens: int,
        max_output_tokens: int,
        timeout: float = 60,
        cache_key: str | None = None,
        business_validator: Callable[[ReaderAnswerEnvelope], None] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
        | None = None,
        max_tool_rounds: int = 3,
    ) -> GatewayResult:
        if not deployment.supports_structured_output:
            raise DependencyPaused(
                f"{deployment.resolved_name}@{deployment.revision} lacks structured-output capability"
            )

        attempts: list[GatewayAttempt] = []
        current_messages = list(messages)
        bounded_tool_rounds = max(0, min(6, int(max_tool_rounds)))

        async def invoke(
            call_messages: list[dict[str, Any]],
            *,
            repair_index: int,
            call_index: int,
            enable_tools: bool,
        ) -> dict[str, Any]:
            reservation_key = f"job:{job_id}:repair:{repair_index}:call:{call_index}"
            request_hash = canonical_hash(
                {
                    "deployment": deployment.lineage,
                    "messages": call_messages,
                    "schema": ReaderAnswerEnvelope.model_json_schema(),
                    "timeout": timeout,
                    "allowed_evidence_ids": sorted(allowed_evidence_ids),
                    "tools": tools if enable_tools else None,
                }
            )
            persistent: PersistentDualAttempt | None = None
            durable_attempt_number = repair_index
            if self.persistence is not None:
                persistent = await self.persistence.reserve_and_start(
                    job_id=job_id,
                    reservation_key=reservation_key,
                    request_hash=request_hash,
                    cache_key=cache_key,
                    input_tokens=max_input_tokens,
                    output_tokens=max_output_tokens,
                    input_price_per_million=deployment.input_price_per_million,
                    output_price_per_million=deployment.output_price_per_million,
                )
                durable_attempt_number = persistent.attempt_number
                reservation_key = persistent.reservation_key
            else:
                budget.reserve(
                    reservation_key,
                    input_tokens=max_input_tokens,
                    output_tokens=max_output_tokens,
                    input_price_per_million=deployment.input_price_per_million,
                    output_price_per_million=deployment.output_price_per_million,
                )

            request: dict[str, Any] = {
                "model": deployment.resolved_name,
                "messages": call_messages,
                "timeout": timeout,
                "num_retries": 0,
                "stream": False,
                "max_tokens": max_output_tokens,
            }
            if enable_tools and tools and tool_executor is not None:
                request["tools"] = tools
            else:
                request["response_format"] = ReaderAnswerEnvelope

            started = time.perf_counter()
            try:
                response = await self.transport.complete(**request)
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                if persistent is not None:
                    await self.persistence.mark_outcome_unknown(
                        persistent,
                        latency_ms=latency_ms,
                        error_code=type(exc).__name__,
                    )
                else:
                    budget.release(reservation_key)
                attempts.append(
                    GatewayAttempt(
                        durable_attempt_number,
                        "outcome_unknown",
                        reservation_key,
                        request_hash,
                        error_code=type(exc).__name__,
                        latency_ms=latency_ms,
                    )
                )
                detail = f"{type(exc).__name__}: {str(exc)[:180]}".replace("\n", " ")
                raise ModelCallFailed(
                    f"provider call outcome is unknown ({detail})", attempts
                ) from exc

            usage = _response_usage(response)
            in_price = deployment.input_price_per_million or Decimal(0)
            out_price = deployment.output_price_per_million or Decimal(0)
            actual_cost = (
                Decimal(usage["input_tokens"]) * in_price
                + Decimal(usage["output_tokens"]) * out_price
            ) / Decimal(1_000_000)
            return {
                "response": response,
                "usage": usage,
                "actual_cost": actual_cost,
                "response_hash": canonical_hash(response),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "persistent": persistent,
                "reservation_key": reservation_key,
                "request_hash": request_hash,
                "attempt_number": durable_attempt_number,
            }

        async def settle(
            state: dict[str, Any],
            *,
            status: str,
            error_code: str | None,
            envelope: dict[str, Any] | None = None,
        ) -> None:
            persistent = state["persistent"]
            persisted_status = "succeeded" if status == "tool_call" else status
            if persistent is not None:
                await self.persistence.complete_attempt(
                    persistent,
                    status=persisted_status,
                    response_hash=state["response_hash"],
                    provider_request_id=_response_request_id(state["response"]),
                    usage=state["usage"],
                    cost_usd=state["actual_cost"],
                    latency_ms=state["latency_ms"],
                    error_code=error_code,
                    envelope=envelope,
                )
            else:
                budget.settle(
                    state["reservation_key"],
                    actual_input_tokens=state["usage"]["input_tokens"],
                    actual_output_tokens=state["usage"]["output_tokens"],
                    actual_cost_usd=state["actual_cost"],
                )
            attempts.append(
                GatewayAttempt(
                    state["attempt_number"],
                    status,
                    state["reservation_key"],
                    state["request_hash"],
                    state["response_hash"],
                    state["usage"],
                    state["actual_cost"],
                    error_code,
                    state["latency_ms"],
                    envelope,
                )
            )

        for repair_index in (1, 2):
            working_messages = list(current_messages)
            tool_round = 0
            call_index = 0
            while True:
                enable_tools = bool(
                    tools and tool_executor is not None and tool_round < bounded_tool_rounds
                )
                state = await invoke(
                    working_messages,
                    repair_index=repair_index,
                    call_index=call_index,
                    enable_tools=enable_tools,
                )
                call_index += 1
                response = state["response"]
                tool_calls = _response_tool_calls(response)
                if tool_calls and tool_executor is not None and enable_tools:
                    tool_round += 1
                    assistant_call_message = {
                        "role": "assistant",
                        "content": _response_content(response)
                        if (isinstance(response, dict) and response.get("content"))
                        else "",
                        "tool_calls": tool_calls,
                    }
                    working_messages.append(assistant_call_message)
                    for call in tool_calls:
                        function = call["function"]
                        try:
                            tool_result = await tool_executor(
                                function["name"], function["arguments"]
                            )
                        except Exception:
                            tool_result = {"results": [], "error": "tool_failed"}
                        if not isinstance(tool_result, dict):
                            tool_result = {"result": tool_result}
                        working_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "name": function["name"],
                                "content": json.dumps(
                                    tool_result, ensure_ascii=False, sort_keys=True
                                ),
                            }
                        )
                    await settle(state, status="tool_call", error_code=None)
                    continue

                try:
                    content = _response_content(response)
                    output = ReaderAnswerEnvelope.model_validate_json(
                        content, strict=True
                    )
                    business_validate_answer(
                        output, allowed_evidence_ids=allowed_evidence_ids
                    )
                    if business_validator is not None:
                        business_validator(output)
                except (ValidationError, ValueError) as exc:
                    await settle(
                        state, status="failed", error_code=type(exc).__name__
                    )
                    if repair_index == 2:
                        detail = str(exc)[:240].replace("\n", " ")
                        raise StructuredOutputRejected(
                            f"structured output failed local validation ({detail})",
                            attempts,
                        ) from exc
                    err_hint = str(exc)[:500].replace("\n", " ")
                    current_messages = working_messages + [
                        {
                            "role": "user",
                            "content": (
                                "Local validation error. Return one corrected JSON object "
                                "matching reader-answer.v1; cite only allowed_evidence_ids; "
                                "do not add fields. Error: "
                                f"{err_hint}"
                            ),
                        }
                    ]
                    break

                envelope_dict = output.model_dump(mode="json")
                await settle(
                    state,
                    status="succeeded",
                    error_code=None,
                    envelope=envelope_dict,
                )
                return GatewayResult(
                    output,
                    attempts,
                    deployment,
                    state["response_hash"],
                )

        raise StructuredOutputRejected(
            "structured output failed after primary and repair attempts",
            attempts,
        )


# Re-export budget exceptions for worker convenience
__all__ = [
    "BudgetExceeded",
    "DependencyPaused",
    "DualBudgetGate",
    "GatewayAttempt",
    "GatewayResult",
    "ModelCallFailed",
    "ModelDeployment",
    "ModelTransport",
    "ReaderChatGateway",
    "StructuredOutputRejected",
    "UnknownPricing",
    "business_validate_answer",
    "canonical_hash",
]
