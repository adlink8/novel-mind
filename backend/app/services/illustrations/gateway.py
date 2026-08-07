"""Provider-neutral illustration generation gateway (Phase 33-02, REQ-VIS-04).

D-33-01..D-33-03: each generation request is an idempotent durable job and its
provider output is an immutable candidate AssetRevision. This module is the
provider-neutral call seam (the ``reader_chat/gateway.py`` analog):

- ``IllustrationTransport`` — the provider-neutral transport contract. A
  transport returns a ``ProviderResponse`` with deterministic bytes, MIME,
  dimensions, request id and usage; it never carries secrets.
- ``IllustrationBudget`` — the reserve/settle seam used *before* every
  provider call. The durable worker satisfies it with a
  ``DurableIllustrationBudgetRepository`` (ledger + reservation rows); the
  Phase 33-01 in-memory ``IllustrationBudgetGate`` is wrapped for unit tests.
- ``IllustrationGateway.generate`` — reserve → call → settle. A transport
  exception is an explicit ``ProviderOutcomeUnknown`` (the provider may have
  created an asset; reconcile by request id/hash); an empty or invalid payload
  is an explicit ``ProviderRejected`` — a provider failure never becomes a
  successful empty asset (D-33-01).
- ``MockIllustrationTransport`` — the deterministic mock provider
  (``illustration-mock-success`` fixture): deterministic bytes/usage/request id
  and injectable ``timeout`` / ``server_5xx`` / ``connection_unknown`` /
  ``empty`` failure modes for the durable-worker failure tests.
- ``check_generation_prompt_gate`` — the server-side generation entrypoint
  gate: the API/worker only accept an **approved** PromptRevision whose Visual
  Bible / source-snapshot lineage is **not stale** (fail closed otherwise).
- ``build_illustration_lineage`` / ``illustration_generation_config_hash`` —
  byte-replayable owner/novel/SceneSpec/prompt/model/config lineage that keys
  the idempotency key (D-33-01).

Provider error messages are redacted (``redact_provider_error``) so no
provider detail or secret leaks into the durable attempt row.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_revision import PromptRevision as PromptRevisionRow
from app.schemas.illustration import (
    IllustrationLineage,
    PriceSnapshot,
    canonical_illustration_hash,
)
from app.schemas.scene_spec import SpecReviewState
from app.services.illustrations.budget import BudgetExceeded, UnknownPricing
from app.services.prompt_compiler.adapters import (
    PromptRevisionNotFound,
    PromptRevisionService,
)

# ---------------------------------------------------------------------------
# Failure vocabulary (D-33-01/D-33-02): explicit, reason-coded, redacted
# ---------------------------------------------------------------------------


class IllustrationGatewayError(RuntimeError):
    """Base class for fail-closed illustration gateway errors."""


class ProviderOutcomeUnknown(IllustrationGatewayError):
    """A transport call ended without a known outcome (timeout/5xx/disconnect).

    The provider may have created an asset; the outcome must be reconciled by
    request id/hash. Never relabeled a success and never becomes an empty asset
    (D-33-01/D-33-02).
    """

    def __init__(self, message: str, attempt: "GatewayAttempt") -> None:
        super().__init__(message)
        self.attempt = attempt


class ProviderRejected(IllustrationGatewayError):
    """A transport call returned a known-bad payload (empty/invalid bytes).

    The provider consumed capacity but produced no usable asset; the budget is
    settled with explicit unknown usage and the attempt is recorded as failed.
    """

    def __init__(self, message: str, attempt: "GatewayAttempt") -> None:
        super().__init__(message)
        self.attempt = attempt


# ---------------------------------------------------------------------------
# Provider-neutral transport contract
# ---------------------------------------------------------------------------


@runtime_checkable
class IllustrationTransport(Protocol):
    async def generate(self, **kwargs: Any) -> "ProviderResponse": ...


@dataclass(frozen=True)
class ProviderResponse:
    """Deterministic provider output; never carries secrets (D-33-03)."""

    payload: bytes
    mime_type: str
    width: int
    height: int
    provider: str
    provider_model: str
    provider_request_id: str
    usage: dict[str, int] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayAttempt:
    """One auditable provider call (mirrors IllustrationAttempt row)."""

    attempt_number: int
    status: str
    reservation_key: str
    request_hash: str
    response_hash: str | None = None
    reservation_id: int | None = None
    provider_request_id: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: Decimal | None = None
    error_code: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class GatewayResult:
    """Successful provider call: deterministic bytes + auditable attempt."""

    response: ProviderResponse
    response_hash: str
    attempt: GatewayAttempt


# ---------------------------------------------------------------------------
# Budget seam (D-33-02): reserve before call, settle with actual usage
# ---------------------------------------------------------------------------


class IllustrationBudget(Protocol):
    async def reserve(
        self,
        *,
        key: str,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        price_snapshot: PriceSnapshot,
    ) -> Any:
        """Reserve worst-case capacity or fail closed (BudgetExceeded)."""

    async def settle(
        self,
        *,
        key: str,
        actual_input_tokens: int,
        actual_output_tokens: int,
        actual_cost_usd: Decimal,
    ) -> None:
        """Settle with explicit actual usage/cost."""

    async def settle_unknown(self, *, key: str, error_code: str) -> None:
        """Settle an unknown-outcome call: usage/cost stays explicitly unknown."""

    async def release(self, *, key: str) -> None:
        """Release a reservation that never reached the transport."""


class AsyncBudgetGate:
    """Adapter wrapping the Phase 33-01 in-memory gate into the async seam."""

    def __init__(self, gate: Any) -> None:
        self._gate = gate

    async def reserve(self, **kwargs: Any) -> Any:
        return self._gate.reserve(**kwargs)

    async def settle(self, **kwargs: Any) -> None:
        self._gate.settle(**kwargs)

    async def settle_unknown(self, *, key: str, error_code: str) -> None:
        self._gate.settle_unknown(key, error_code=error_code)

    async def release(self, *, key: str) -> None:
        self._gate.release(key)


# ---------------------------------------------------------------------------
# Canonical hashing + provider error redaction
# ---------------------------------------------------------------------------


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


# Patterns likely to leak provider credentials into error text.
_SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)https?://[^\s]+@[^\s]+"),
)


def redact_provider_error(exc: Exception, *, max_chars: int = 120) -> str:
    """Sanitize a provider exception into a redacted, stable reason string."""
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Generation entrypoint gate (approved + fresh; fail closed)
# ---------------------------------------------------------------------------


class GenerationGateError(RuntimeError):
    """The generation entrypoint rejected a PromptRevision candidate."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


async def check_generation_prompt_gate(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    prompt_revision_id: int,
) -> PromptRevisionRow:
    """Only an **approved** and **non-stale** PromptRevision may generate.

    - a prompt outside the explicit owner/novel scope is indistinguishable from
      "not found" (404-equivalent, D-33-03 owner containment);
    - an unapproved prompt can never generate (candidate-only);
    - a stale prompt (compiled against a superseded Visual Bible revision or
      source snapshot) fails closed and must be recompiled first.
    """
    service = PromptRevisionService(session)
    try:
        view, stale = await service.load(
            owner_id=owner_id,
            novel_id=novel_id,
            revision_id=prompt_revision_id,
        )
    except PromptRevisionNotFound:
        raise GenerationGateError(
            "prompt_revision_not_found",
            "prompt revision not found in the explicit owner/novel scope",
        ) from None
    if view.review_state is not SpecReviewState.APPROVED:
        raise GenerationGateError(
            "prompt_not_approved",
            f"prompt revision review_state={view.review_state.value!r}; only an "
            "approved prompt is a valid generation input",
        )
    if stale:
        raise GenerationGateError(
            "stale_prompt",
            "prompt is stale (superseded Visual Bible revision or source "
            "snapshot); recompile before generation",
        )
    row = await session.scalar(
        select(PromptRevisionRow).where(
            PromptRevisionRow.owner_id == owner_id,
            PromptRevisionRow.novel_id == novel_id,
            PromptRevisionRow.id == prompt_revision_id,
        )
    )
    if row is None:
        raise GenerationGateError(
            "prompt_revision_not_found",
            "prompt revision not found in the explicit owner/novel scope",
        )
    return row


# ---------------------------------------------------------------------------
# Byte-replayable generation lineage (D-33-01)
# ---------------------------------------------------------------------------


def illustration_generation_config_hash(
    *,
    provider: str,
    model: str,
    width: int,
    height: int,
    max_input_tokens: int,
    max_output_tokens: int,
) -> str:
    """Deterministic generation config hash (a lineage dimension)."""
    return canonical_illustration_hash(
        {
            "kind": "illustration_generation.config",
            "provider": provider,
            "model": model,
            "width": width,
            "height": height,
            "max_input_tokens": max_input_tokens,
            "max_output_tokens": max_output_tokens,
        }
    )


def build_illustration_lineage(
    *,
    prompt_revision: PromptRevisionRow,
    provider: str,
    model: str,
    width: int,
    height: int,
    max_input_tokens: int,
    max_output_tokens: int,
) -> IllustrationLineage:
    """Freeze the owner/novel/SceneSpec/prompt/model/config lineage.

    Every value replays from the prompt revision row and the generation config
    so the job idempotency key is byte-replayable (D-33-01).
    """
    return IllustrationLineage(
        scene_spec_hash=prompt_revision.scene_spec_hash,
        prompt_revision_id=prompt_revision.id,
        prompt_revision_hash=prompt_revision.prompt_hash,
        visual_bible_revision_id=prompt_revision.visual_bible_revision_id,
        visual_bible_revision_hash=prompt_revision.visual_bible_revision_hash,
        source_snapshot_id=prompt_revision.source_snapshot_id,
        source_snapshot_hash=prompt_revision.source_snapshot_hash,
        cutoff_chapter=prompt_revision.cutoff_chapter,
        model_lineage={"provider": provider, "model": model, "revision": model},
        config_hash=illustration_generation_config_hash(
            provider=provider,
            model=model,
            width=width,
            height=height,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
        ),
    )


# ---------------------------------------------------------------------------
# Gateway: reserve → call → settle (D-33-02)
# ---------------------------------------------------------------------------


class IllustrationGateway:
    """Owns provider-neutral generation calls; the worker owns durability."""

    def __init__(self, transport: IllustrationTransport) -> None:
        self.transport = transport

    async def generate(
        self,
        *,
        job_id: int,
        attempt_number: int,
        reservation_key: str,
        prompt_text: str,
        lineage: IllustrationLineage,
        price_snapshot: PriceSnapshot,
        budget: IllustrationBudget,
        max_input_tokens: int,
        max_output_tokens: int,
        width: int,
        height: int,
        timeout: float = 30.0,
    ) -> GatewayResult:
        request_hash = canonical_hash(
            {
                "job_id": job_id,
                "attempt_number": attempt_number,
                "model_lineage": lineage.model_lineage,
                "config_hash": lineage.config_hash,
                "scene_spec_hash": lineage.scene_spec_hash,
                "prompt_revision_hash": lineage.prompt_revision_hash,
                "width": width,
                "height": height,
                "prompt": prompt_text,
            }
        )
        reservation_id: int | None = None
        try:
            reservation = await budget.reserve(
                key=reservation_key,
                calls=1,
                input_tokens=max_input_tokens,
                output_tokens=max_output_tokens,
                price_snapshot=price_snapshot,
            )
            reservation_id = getattr(reservation, "id", None)
        except (UnknownPricing, BudgetExceeded):
            # No provider call happened; the worker pauses the job (D-33-02).
            raise

        started = time.perf_counter()
        try:
            response = await self.transport.generate(
                prompt=prompt_text,
                width=width,
                height=height,
                timeout=timeout,
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - durable outcome isolation
            latency_ms = int((time.perf_counter() - started) * 1000)
            error_code = type(exc).__name__
            await budget.settle_unknown(key=reservation_key, error_code=error_code)
            # transport 显式拒绝（4xx 坏请求等）→ ProviderRejected（已决失败，
            # 不留空资产、不重试）；其余 → ProviderOutcomeUnknown（可对账）。
            if getattr(exc, "provider_rejected", False):
                raise ProviderRejected(
                    f"provider rejected request ({redact_provider_error(exc)})",
                    GatewayAttempt(
                        attempt_number=attempt_number,
                        status="failed",
                        reservation_key=reservation_key,
                        request_hash=request_hash,
                        reservation_id=reservation_id,
                        error_code=error_code,
                        latency_ms=latency_ms,
                    ),
                ) from exc
            raise ProviderOutcomeUnknown(
                f"provider outcome is unknown ({redact_provider_error(exc)})",
                GatewayAttempt(
                    attempt_number=attempt_number,
                    status="outcome_unknown",
                    reservation_key=reservation_key,
                    request_hash=request_hash,
                    reservation_id=reservation_id,
                    error_code=error_code,
                    latency_ms=latency_ms,
                ),
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        response_hash = hashlib.sha256(response.payload).hexdigest()

        if not response.payload:
            await budget.settle_unknown(key=reservation_key, error_code="empty_asset")
            raise ProviderRejected(
                "provider returned an empty asset; it cannot become a successful "
                "AssetRevision (D-33-01)",
                GatewayAttempt(
                    attempt_number=attempt_number,
                    status="failed",
                    reservation_key=reservation_key,
                    request_hash=request_hash,
                    reservation_id=reservation_id,
                    provider_request_id=response.provider_request_id,
                    response_hash=response_hash,
                    error_code="empty_asset",
                    latency_ms=latency_ms,
                ),
            )

        usage = dict(response.usage)
        actual_cost = _cost_usd(price_snapshot, usage)
        await budget.settle(
            key=reservation_key,
            actual_input_tokens=usage.get("input_tokens", 0),
            actual_output_tokens=usage.get("output_tokens", 0),
            actual_cost_usd=actual_cost,
        )
        attempt = GatewayAttempt(
            attempt_number=attempt_number,
            status="succeeded",
            reservation_key=reservation_key,
            request_hash=request_hash,
            reservation_id=reservation_id,
            provider_request_id=response.provider_request_id,
            response_hash=response_hash,
            usage=usage,
            cost_usd=actual_cost,
            latency_ms=latency_ms,
        )
        return GatewayResult(
            response=response, response_hash=response_hash, attempt=attempt
        )


def _cost_usd(price_snapshot: PriceSnapshot, usage: dict[str, int]) -> Decimal:
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    image_cost = price_snapshot.image_price_per_image or Decimal(0)
    token_cost = (
        Decimal(input_tokens) * (price_snapshot.input_price_per_million or Decimal(0))
        + Decimal(output_tokens)
        * (price_snapshot.output_price_per_million or Decimal(0))
    ) / Decimal(1_000_000)
    return image_cost + token_cost


# ---------------------------------------------------------------------------
# Deterministic mock provider (illustration-mock-success fixture)
# ---------------------------------------------------------------------------


class MockIllustrationTransport:
    """Deterministic provider: bytes/usage/request id replay from the prompt.

    ``mode`` selects the fixture behavior:
    - ``success``: deterministic PNG bytes + usage + request id;
    - ``timeout``: raises TimeoutError (outcome unknown);
    - ``server_5xx``: raises RuntimeError("provider returned 503");
    - ``connection_unknown``: raises ConnectionError (outcome unknown);
    - ``empty``: returns an empty payload (explicit rejection, never success).
    """

    def __init__(
        self,
        *,
        mode: str = "success",
        provider: str = "mock",
        model: str = "mock-img-v1",
        input_tokens: int = 120,
        output_tokens: int = 256,
        width: int = 1024,
        height: int = 1024,
        fail_first: int = 0,
    ) -> None:
        self.mode = mode
        self.provider = provider
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.width = width
        self.height = height
        self.fail_first = fail_first
        self.calls = 0

    async def generate(self, **kwargs: Any) -> ProviderResponse:
        self.calls += 1
        prompt = str(kwargs.get("prompt") or "")
        if self.calls <= self.fail_first:
            # Bounded failure injection for retry tests: the first ``fail_first``
            # calls are timeouts (outcome unknown), then the mode takes over.
            raise TimeoutError("mock provider timed out")
        if self.mode == "timeout":
            raise TimeoutError("mock provider timed out")
        if self.mode == "server_5xx":
            raise RuntimeError("mock provider returned HTTP 503")
        if self.mode == "connection_unknown":
            raise ConnectionError("mock provider connection reset")
        if self.mode == "empty":
            return ProviderResponse(
                payload=b"",
                mime_type="image/png",
                width=self.width,
                height=self.height,
                provider=self.provider,
                provider_model=self.model,
                provider_request_id="mock-empty",
                usage={"input_tokens": 0, "output_tokens": 0},
                response_metadata={"mode": "empty"},
            )
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        payload = b"\x89PNG\r\n\x1a\n" + digest.encode("ascii") + b"mock-image"
        return ProviderResponse(
            payload=payload,
            mime_type="image/png",
            width=self.width,
            height=self.height,
            provider=self.provider,
            provider_model=self.model,
            provider_request_id=f"mock-req-{digest[:16]}",
            usage={
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
            response_metadata={
                "mode": "success",
                "fixture": "illustration-mock-success",
            },
        )


# Re-export budget exceptions for worker convenience.
__all__ = [
    "AsyncBudgetGate",
    "BudgetExceeded",
    "GatewayAttempt",
    "GatewayResult",
    "GenerationGateError",
    "IllustrationBudget",
    "IllustrationGateway",
    "IllustrationGatewayError",
    "IllustrationTransport",
    "MockIllustrationTransport",
    "ProviderOutcomeUnknown",
    "ProviderRejected",
    "ProviderResponse",
    "UnknownPricing",
    "build_illustration_lineage",
    "canonical_hash",
    "check_generation_prompt_gate",
    "illustration_generation_config_hash",
    "redact_provider_error",
]
