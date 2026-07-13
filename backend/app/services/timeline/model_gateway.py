"""Strict, timeline-only structured model gateway with explicit repair semantics."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.services.timeline.budget import BudgetGate

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class DependencyPaused(RuntimeError):
    """Resolved deployment cannot safely execute this structured stage."""


class ModelCallFailed(RuntimeError):
    def __init__(self, message: str, attempts: list["GatewayAttempt"]) -> None:
        super().__init__(message)
        self.attempts = attempts


class StructuredOutputRejected(ModelCallFailed):
    """Both the original response and the sole repair failed local gates."""


class ModelTransport(Protocol):
    async def complete(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class ModelDeployment:
    provider: str
    model_id: str
    revision: str
    supports_structured_output: bool
    input_price_per_million: Decimal
    output_price_per_million: Decimal

    @property
    def resolved_name(self) -> str:
        return f"{self.provider}/{self.model_id}"

    @property
    def lineage(self) -> tuple[str, str, str]:
        return self.provider, self.model_id, self.revision


@dataclass(frozen=True)
class GatewayAttempt:
    attempt_number: int
    status: str
    reservation_key: str
    request_hash: str
    response_hash: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    error_code: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class GatewayResult(Generic[SchemaT]):
    output: SchemaT
    attempts: list[GatewayAttempt]
    deployment: ModelDeployment


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def _response_content(response: Any) -> str:
    if isinstance(response, dict) and isinstance(response.get("content"), str):
        return response["content"]
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    raise ValueError("provider response has no textual structured content")


def _response_usage(response: Any) -> dict[str, int]:
    raw = response.get("usage", {}) if isinstance(response, dict) else getattr(response, "usage", {})
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    return {
        "input_tokens": int(raw.get("input_tokens", raw.get("prompt_tokens", 0))),
        "output_tokens": int(raw.get("output_tokens", raw.get("completion_tokens", 0))),
    }


class TimelineModelGateway:
    """Owns timeline structured calls; callers own persistence and publication."""

    def __init__(self, transport: ModelTransport) -> None:
        self.transport = transport

    async def generate(
        self, *, deployment: ModelDeployment, schema: type[SchemaT],
        messages: list[dict[str, str]], budget: BudgetGate, run_id: int,
        stage_key: str, max_input_tokens: int, max_output_tokens: int,
        timeout: float = 60, business_validator: Callable[[SchemaT], None] | None = None,
    ) -> GatewayResult[SchemaT]:
        if not deployment.supports_structured_output:
            raise DependencyPaused(
                f"{deployment.resolved_name}@{deployment.revision} lacks structured-output capability"
            )

        attempts: list[GatewayAttempt] = []
        current_messages = list(messages)
        for attempt_number in (1, 2):
            reservation_key = f"{stage_key}:attempt:{attempt_number}"
            budget.reserve(
                reservation_key, input_tokens=max_input_tokens, output_tokens=max_output_tokens,
                input_price_per_million=deployment.input_price_per_million,
                output_price_per_million=deployment.output_price_per_million,
            )
            request_hash = _canonical_hash({
                "deployment": deployment.lineage, "messages": current_messages,
                "schema": schema.model_json_schema(), "timeout": timeout,
            })
            started = time.perf_counter()
            try:
                response = await self.transport.complete(
                    model=deployment.resolved_name, messages=current_messages,
                    response_format=schema, timeout=timeout, num_retries=0, stream=False,
                )
            except Exception as exc:
                attempts.append(GatewayAttempt(
                    attempt_number, "outcome_unknown", reservation_key, request_hash,
                    error_code=type(exc).__name__, latency_ms=int((time.perf_counter() - started) * 1000),
                ))
                raise ModelCallFailed("provider call outcome is unknown", attempts) from exc

            usage = _response_usage(response)
            actual_cost = (
                Decimal(usage["input_tokens"]) * deployment.input_price_per_million
                + Decimal(usage["output_tokens"]) * deployment.output_price_per_million
            ) / Decimal(1_000_000)
            budget.settle(
                reservation_key, actual_input_tokens=usage["input_tokens"],
                actual_output_tokens=usage["output_tokens"], actual_cost_usd=actual_cost,
            )
            try:
                content = _response_content(response)
                output = schema.model_validate_json(content)
                if business_validator is not None:
                    business_validator(output)
            except (ValidationError, ValueError) as exc:
                response_hash = _canonical_hash(response)
                attempts.append(GatewayAttempt(
                    attempt_number, "schema_rejected", reservation_key, request_hash,
                    response_hash, usage, type(exc).__name__,
                    int((time.perf_counter() - started) * 1000),
                ))
                if attempt_number == 2:
                    raise StructuredOutputRejected("structured output failed local validation", attempts) from exc
                current_messages = current_messages + [{
                    "role": "user",
                    "content": "Local validation error. Return one corrected JSON object matching the supplied schema; do not add fields.",
                }]
                continue

            attempts.append(GatewayAttempt(
                attempt_number, "succeeded", reservation_key, request_hash,
                _canonical_hash(response), usage, latency_ms=int((time.perf_counter() - started) * 1000),
            ))
            return GatewayResult(output, attempts, deployment)

        raise AssertionError("unreachable")
