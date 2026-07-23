"""Strict evidence-only reader-chat model gateway with dual budgets and one repair."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Protocol

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
        messages: list[dict[str, str]],
        allowed_evidence_ids: set[str],
        budget: DualBudgetGate,
        job_id: int,
        max_input_tokens: int,
        max_output_tokens: int,
        timeout: float = 60,
        cache_key: str | None = None,
        business_validator: Callable[[ReaderAnswerEnvelope], None] | None = None,
    ) -> GatewayResult:
        if not deployment.supports_structured_output:
            raise DependencyPaused(
                f"{deployment.resolved_name}@{deployment.revision} lacks structured-output capability"
            )

        attempts: list[GatewayAttempt] = []
        current_messages = list(messages)

        for repair_index in (1, 2):
            reservation_key = f"job:{job_id}:repair:{repair_index}"
            request_hash = canonical_hash(
                {
                    "deployment": deployment.lineage,
                    "messages": current_messages,
                    "schema": ReaderAnswerEnvelope.model_json_schema(),
                    "timeout": timeout,
                    "allowed_evidence_ids": sorted(allowed_evidence_ids),
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

            started = time.perf_counter()
            try:
                response = await self.transport.complete(
                    model=deployment.resolved_name,
                    messages=current_messages,
                    response_format=ReaderAnswerEnvelope,
                    timeout=timeout,
                    num_retries=0,
                    stream=False,
                    max_tokens=max_output_tokens,
                )
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
                    f"provider call outcome is unknown ({detail})",
                    attempts,
                ) from exc

            usage = _response_usage(response)
            in_price = deployment.input_price_per_million or Decimal(0)
            out_price = deployment.output_price_per_million or Decimal(0)
            actual_cost = (
                Decimal(usage["input_tokens"]) * in_price
                + Decimal(usage["output_tokens"]) * out_price
            ) / Decimal(1_000_000)
            response_hash = canonical_hash(response)
            latency_ms = int((time.perf_counter() - started) * 1000)

            try:
                content = _response_content(response)
                output = ReaderAnswerEnvelope.model_validate_json(content, strict=True)
                business_validate_answer(
                    output, allowed_evidence_ids=allowed_evidence_ids
                )
                if business_validator is not None:
                    business_validator(output)
            except (ValidationError, ValueError) as exc:
                if persistent is not None:
                    await self.persistence.complete_attempt(
                        persistent,
                        status="failed",
                        response_hash=response_hash,
                        provider_request_id=_response_request_id(response),
                        usage=usage,
                        cost_usd=actual_cost,
                        latency_ms=latency_ms,
                        error_code=type(exc).__name__,
                    )
                else:
                    budget.settle(
                        reservation_key,
                        actual_input_tokens=usage["input_tokens"],
                        actual_output_tokens=usage["output_tokens"],
                        actual_cost_usd=actual_cost,
                    )
                attempts.append(
                    GatewayAttempt(
                        durable_attempt_number,
                        "failed",
                        reservation_key,
                        request_hash,
                        response_hash,
                        usage,
                        actual_cost,
                        type(exc).__name__,
                        latency_ms,
                    )
                )
                if repair_index == 2:
                    detail = str(exc)[:240].replace("\n", " ")
                    raise StructuredOutputRejected(
                        f"structured output failed local validation ({detail})",
                        attempts,
                    ) from exc
                err_hint = str(exc)[:500].replace("\n", " ")
                current_messages = current_messages + [
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
                continue

            envelope_dict = output.model_dump(mode="json")
            if persistent is not None:
                await self.persistence.complete_attempt(
                    persistent,
                    status="succeeded",
                    response_hash=response_hash,
                    provider_request_id=_response_request_id(response),
                    usage=usage,
                    cost_usd=actual_cost,
                    latency_ms=latency_ms,
                    error_code=None,
                    envelope=envelope_dict,
                )
            else:
                budget.settle(
                    reservation_key,
                    actual_input_tokens=usage["input_tokens"],
                    actual_output_tokens=usage["output_tokens"],
                    actual_cost_usd=actual_cost,
                )
            attempts.append(
                GatewayAttempt(
                    durable_attempt_number,
                    "succeeded",
                    reservation_key,
                    request_hash,
                    response_hash,
                    usage,
                    actual_cost,
                    latency_ms=latency_ms,
                    envelope=envelope_dict,
                )
            )
            return GatewayResult(output, attempts, deployment, response_hash)

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
