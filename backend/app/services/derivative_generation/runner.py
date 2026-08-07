"""Budgeted constrained candidate runner (Phase 37-02, D-37-02).

Agent-candidate / script-publish boundary (RESEARCH): the runner accepts only a
sealed ``ContextPackageRecord``, routes one provider call through the existing
``ai_router``/``ai_service``, parses the response into a strict
``CandidateDraft`` and applies the deterministic gate chain:

    sealed package -> ai_router -> strict schema candidate -> deterministic gate

Only a candidate row (``candidate | blocked | needs_override``) is ever
persisted; nothing writes Original Canon or an active pointer (D-37-02). Every
run records prompt/model/package hashes, reserved vs actual usage/cost and the
frozen budget policy; a budget overrun or a schema violation never calls or
publishes, and a terminal job is never silently re-called (recovery is an
explicit re-run of a paused/queued job).

The transport is injectable (fake gateway in tests) and mirrors the
``reader_chat`` ModelTransport protocol; the default transport wraps
``ai_service.chat``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_context import ContextPackageRecord
from app.models.derivative_generation_job import (
    DERIVATIVE_GENERATION_NONTERMINAL_STATUSES,
    DERIVATIVE_GENERATION_SCHEMA_VERSION,
    DerivativeGenerationAttempt,
    DerivativeGenerationCandidate,
    DerivativeGenerationJob,
)
from app.services.ai_router import ModelTier, ai_router
from app.services.ai_service import ai_service
from app.services.derivative_generation.candidate import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateDraft,
    CandidateGateResult,
    GateVerdict,
    apply_deterministic_gates,
    canonical_candidate_hash,
    candidate_hash,
    parse_candidate,
)
from app.services.derivative_generation.context_package import (
    ContextPackageError,
    estimate_input_tokens,
    verify_package_hash,
)

logger = logging.getLogger(__name__)

DERIVATIVE_GENERATION_TASK = "derivative_generation"
DEFAULT_MAX_OUTPUT_TOKENS = 8_000
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_TEMPERATURE = 0.7
DEFAULT_BUDGET_MAX_CALLS = 20
DEFAULT_BUDGET_MAX_INPUT_TOKENS = 20_000
DEFAULT_BUDGET_MAX_OUTPUT_TOKENS = 20_000
DEFAULT_BUDGET_MAX_COST_USD = Decimal("20.00")
# Evidence allowlist is capped inside the prompt; the gate reads the package.
PROMPT_MAX_EVIDENCE_ITEMS = 64

# Stable job status mapping from a gate verdict (D-37-02).
_VERDICT_TO_JOB_STATUS = {
    GateVerdict.CANDIDATE: "succeeded",
    GateVerdict.BLOCKED: "blocked",
    GateVerdict.NEEDS_OVERRIDE: "needs_override",
}

# Stable failure reason codes.
CODE_JOB_NOT_RUNNABLE = "job_not_runnable"
CODE_PACKAGE_NOT_FOUND = "package_not_found"
CODE_PACKAGE_HASH_MISMATCH = "package_hash_mismatch"
CODE_INTENT_MISMATCH = "intent_mismatch"
CODE_BUDGET_EXHAUSTED = "budget_exhausted"
CODE_BUDGET_PAUSED = "budget_paused"
CODE_UNKNOWN_PRICING = "unknown_pricing"
CODE_PROVIDER_TIMEOUT = "provider_timeout"
CODE_PROVIDER_ERROR = "provider_error"
CODE_SCHEMA_INVALID = "schema_invalid"
CODE_CANCELLED = "cancelled"


class CandidateRunError(RuntimeError):
    """Deterministic runner/job gate violation with a stable code."""

    def __init__(self, code: str, detail: str, status_code: int = 409):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


class BudgetExceeded(RuntimeError):
    """The worst-case reservation exceeds the frozen generation policy."""


class UnknownPricing(BudgetExceeded):
    """Deployment pricing is unknown; cost cannot be reserved (fail closed)."""


# ---------------------------------------------------------------------------
# Provider-neutral transport / deployment (reader_chat gateway analog)
# ---------------------------------------------------------------------------


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


def deployment_from_tier(tier: ModelTier) -> ModelDeployment:
    """Provider-neutral deployment from an ai_router tier (cost per 1K -> /1M)."""
    per_million = Decimal(str(tier.cost_per_1k or 0)) * Decimal(1000)
    return ModelDeployment(
        provider=tier.provider,
        model_id=tier.model_id,
        revision="routed",
        supports_structured_output=True,
        input_price_per_million=per_million,
        output_price_per_million=per_million,
    )


class AIServiceTransport:
    """Default transport wrapping the existing ai_service.chat gateway."""

    def __init__(self, service: Any = ai_service) -> None:
        self._service = service

    async def complete(self, **kwargs: Any) -> Any:
        return await self._service.chat(
            messages=kwargs["messages"],
            model=kwargs["model"],
            max_tokens=kwargs.get("max_tokens", DEFAULT_MAX_OUTPUT_TOKENS),
            temperature=kwargs.get("temperature", DEFAULT_TEMPERATURE),
            timeout=kwargs.get("timeout"),
            task_type=DERIVATIVE_GENERATION_TASK,
        )


# ---------------------------------------------------------------------------
# Deterministic prompt compilation (allowlisted package fields only)
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


def compile_prompt(
    package: dict[str, Any],
    *,
    intent: str,
    max_evidence_items: int = PROMPT_MAX_EVIDENCE_ITEMS,
) -> list[dict[str, str]]:
    """Deterministic prompt from allowlisted sealed-package fields only.

    Model-independent (the deployment lineage is recorded separately), so the
    same package + intent always compiles the same prompt and ``prompt_hash``.
    """
    dimensions = package.get("dimensions") or {}
    evidence_items = ((dimensions.get("evidence") or {}).get("items") or [])[
        :max_evidence_items
    ]
    evidence_keys = sorted(
        str(item["candidate_key"])
        for item in evidence_items
        if isinstance(item, dict) and item.get("candidate_key")
    )
    world_state = (dimensions.get("world_state") or {}).get("items") or []
    timeline = (dimensions.get("timeline") or {}).get("items") or []
    unresolved_clues = (dimensions.get("unresolved_clues") or {}).get("items") or []
    world_rules = (dimensions.get("world_rules") or {}).get("items") or []

    system = (
        "You are the constrained derivative writer for a Fanfiction Canon Fork.\n"
        "You produce ONLY a strict JSON object matching 'derivative-candidate.v1'.\n"
        "Rules:\n"
        "- Cite only the evidence keys listed in the sealed package below.\n"
        "- Never assert a fact, clue payoff, or causality the package does not contain.\n"
        "- Your output is a candidate only: never write to Original Canon.\n"
        "- To diverge intentionally, return an explicit 'divergence' CanonDelta "
        "with divergence_type, reason, affected_evidence and scope='derivative'.\n"
        "- Every 'branch_suggestions' entry must carry enabled_by_default=false "
        "and a canon_delta_hash.\n"
        "- Do not add any field outside the schema."
    )
    user_payload: dict[str, Any] = {
        "intent": intent,
        "fork": package.get("version") or {},
        "world_state": world_state,
        "timeline": timeline,
        "unresolved_clues": unresolved_clues,
        "world_rules": world_rules,
        "allowed_evidence_keys": evidence_keys,
    }
    user_body = json.dumps(
        user_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    user = (
        "Write the next {intent} passage for the frozen fork scope below. "
        "Return one strict JSON object. Do not include commentary.\n\n"
        "{body}"
    ).format(intent=intent, body=user_body)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def prompt_hash(package: dict[str, Any], *, intent: str) -> str:
    """Deterministic prompt lineage (schema + messages)."""
    return canonical_hash(
        {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "schema": CandidateDraft.model_json_schema(),
            "intent": intent,
            "messages": compile_prompt(package, intent=intent),
        }
    )


def config_hash(
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Deterministic decoding config lineage (frozen at job creation)."""
    return canonical_hash(
        {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "timeout": timeout,
        }
    )


def build_generation_idempotency_key(
    owner_id: int,
    novel_id: int,
    *,
    package_hash: str,
    intent: str,
    job_key: str,
) -> str:
    """D-37-02 idempotency key: one nonterminal job per lineage, one charge."""
    return canonical_hash(
        {
            "artifact_kind": "derivative_generation_job",
            "schema_version": DERIVATIVE_GENERATION_SCHEMA_VERSION,
            "owner_id": owner_id,
            "novel_id": novel_id,
            "package_hash": package_hash,
            "intent": intent,
            "job_key": job_key,
        }
    )


# ---------------------------------------------------------------------------
# In-memory budget gate (ScopeBudgetGate analog, single scope)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivativeBudgetPolicy:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_usd: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_usd": str(self.max_cost_usd),
        }


DEFAULT_DERIVATIVE_BUDGET = DerivativeBudgetPolicy(
    max_calls=DEFAULT_BUDGET_MAX_CALLS,
    max_input_tokens=DEFAULT_BUDGET_MAX_INPUT_TOKENS,
    max_output_tokens=DEFAULT_BUDGET_MAX_OUTPUT_TOKENS,
    max_cost_usd=DEFAULT_BUDGET_MAX_COST_USD,
)


@dataclass
class Reservation:
    key: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    status: str = "reserved"


class DerivativeBudgetGate:
    """In-memory single-scope gate; a policy overrun pauses the gate forever."""

    def __init__(self, policy: DerivativeBudgetPolicy) -> None:
        self.policy = policy
        self.reservations: dict[str, Reservation] = {}
        self.paused = False

    @property
    def network_calls_allowed(self) -> bool:
        return not self.paused

    def reserve(
        self,
        key: str,
        *,
        input_tokens: int,
        output_tokens: int,
        input_price_per_million: Decimal | None,
        output_price_per_million: Decimal | None,
    ) -> Reservation:
        existing = self.reservations.get(key)
        if existing:
            return existing
        if self.paused:
            raise BudgetExceeded("generation budget is paused; no further calls")
        if input_price_per_million is None or output_price_per_million is None:
            self.paused = True
            raise UnknownPricing("provider pricing is unknown; cost cannot be reserved")
        cost = (
            Decimal(input_tokens) * input_price_per_million
            + Decimal(output_tokens) * output_price_per_million
        ) / Decimal(1_000_000)
        active = [r for r in self.reservations.values() if r.status == "reserved"]
        if (
            len(active) + 1 > self.policy.max_calls
            or sum(r.input_tokens for r in active) + input_tokens
            > self.policy.max_input_tokens
            or sum(r.output_tokens for r in active) + output_tokens
            > self.policy.max_output_tokens
            or sum((r.cost_usd for r in active), Decimal(0)) + cost
            > self.policy.max_cost_usd
        ):
            self.paused = True
            raise BudgetExceeded("worst-case reservation exceeds frozen policy")
        reservation = Reservation(key, input_tokens, output_tokens, cost)
        self.reservations[key] = reservation
        return reservation

    def settle(
        self,
        key: str,
        *,
        actual_input_tokens: int,
        actual_output_tokens: int,
        actual_cost_usd: Decimal,
    ) -> None:
        reservation = self.reservations.get(key)
        if reservation is None or reservation.status != "reserved":
            raise ValueError("only reserved entries can be settled")
        if (
            actual_input_tokens > reservation.input_tokens
            or actual_output_tokens > reservation.output_tokens
        ):
            self.paused = True
            raise BudgetExceeded("provider usage exceeded reservation")
        reservation.input_tokens = actual_input_tokens
        reservation.output_tokens = actual_output_tokens
        reservation.cost_usd = actual_cost_usd
        reservation.status = "settled"

    def release(self, key: str) -> None:
        reservation = self.reservations.get(key)
        if reservation is None or reservation.status != "reserved":
            raise ValueError("only reserved entries can be released")
        reservation.status = "released"


# ---------------------------------------------------------------------------
# Response parsing helpers (dict or object responses)
# ---------------------------------------------------------------------------


def _response_content(response: Any) -> str:
    if isinstance(response, dict) and isinstance(response.get("content"), str):
        text = response["content"]
    else:
        content = getattr(response, "content", None)
        if not isinstance(content, str):
            raise ValueError("provider response has no textual structured content")
        text = content
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
    value = (
        response.get("id")
        if isinstance(response, dict)
        else getattr(response, "id", None)
    )
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateRunResult:
    job: DerivativeGenerationJob
    candidate: DerivativeGenerationCandidate | None
    attempts: list[DerivativeGenerationAttempt]
    status: str
    error_code: str | None = None
    gate_verdict: str | None = None
    gate_reason: str | None = None


class DerivativeCandidateRunner:
    """Runs one budgeted candidate generation job (lease-free, synchronous)."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        transport: ModelTransport,
        budget_gate: DerivativeBudgetGate | None = None,
        router: Any = ai_router,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        self._session = session
        self._transport = transport
        self._budget_gate = budget_gate or DerivativeBudgetGate(
            DEFAULT_DERIVATIVE_BUDGET
        )
        self._router = router
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout
        self._temperature = temperature

    @property
    def budget_policy(self) -> DerivativeBudgetPolicy:
        """The frozen policy the runner reserves against (audit snapshot)."""
        return self._budget_gate.policy

    async def run(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> CandidateRunResult:
        job = await self._load_scoped_job(owner_id, novel_id, job_id)
        if job.status not in DERIVATIVE_GENERATION_NONTERMINAL_STATUSES:
            raise CandidateRunError(
                CODE_JOB_NOT_RUNNABLE,
                f"job {job.id} is {job.status!r}; terminal jobs are never "
                "silently re-called",
            )
        if job.cancel_requested:
            job.status = "cancelled"
            job.status_reason = "cancelled before the run started"
            job.error_code = CODE_CANCELLED
            await self._session.flush()
            return await self._finish(job, None, [], "cancelled", CODE_CANCELLED)

        job.status = "running"
        job.retry_count += 1
        await self._session.flush()

        attempt_number = await self._next_attempt_number(job.id)

        # ---- sealed package gate (T-37-02-01) ----
        package = await self._load_scoped_package(owner_id, novel_id, job)
        if package is None:
            return await self._fail(
                job,
                CODE_PACKAGE_NOT_FOUND,
                "context package is outside the owner/novel scope",
            )
        payload = dict(package.canonical_payload or {})
        try:
            verify_package_hash(payload, job.package_hash)
        except ContextPackageError as exc:
            return await self._fail(job, CODE_PACKAGE_HASH_MISMATCH, exc.detail)
        if payload.get("intent") != job.intent:
            return await self._fail(
                job,
                CODE_INTENT_MISMATCH,
                f"sealed package intent {payload.get('intent')!r} != job intent {job.intent!r}",
            )
        # Lineage drift fails closed: the run must replay the frozen prompt/config.
        if prompt_hash(payload, intent=job.intent) != job.prompt_hash:
            return await self._fail(
                job,
                "prompt_hash_mismatch",
                "the frozen prompt hash does not replay from the sealed package",
            )
        if (
            config_hash(
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                timeout=self._timeout,
            )
            != job.config_hash
        ):
            return await self._fail(
                job,
                "config_hash_mismatch",
                "the runner decoding config does not replay the frozen config hash",
            )

        # ---- budget gate before any provider call (D-37-02) ----
        budget_estimate = payload.get("budget_estimate") or {}
        input_tokens = int(budget_estimate.get("estimated_input_tokens") or 0)
        if input_tokens < 1:
            input_tokens = estimate_input_tokens(payload)
        reservation_key = f"job:{job.id}:attempt:{attempt_number}"

        tier = self._router.route_task(DERIVATIVE_GENERATION_TASK)
        deployment = deployment_from_tier(tier)
        request_hash = self._request_hash(job, deployment)
        try:
            reservation = self._budget_gate.reserve(
                reservation_key,
                input_tokens=input_tokens,
                output_tokens=self._max_output_tokens,
                input_price_per_million=deployment.input_price_per_million,
                output_price_per_million=deployment.output_price_per_million,
            )
        except UnknownPricing as exc:
            attempt = await self._record_rejected_attempt(
                job, attempt_number, deployment, request_hash, CODE_UNKNOWN_PRICING
            )
            return await self._pause_budget(
                job, CODE_UNKNOWN_PRICING, str(exc), attempts=[attempt]
            )
        except BudgetExceeded as exc:
            attempt = await self._record_rejected_attempt(
                job, attempt_number, deployment, request_hash, CODE_BUDGET_EXHAUSTED
            )
            return await self._pause_budget(
                job, CODE_BUDGET_EXHAUSTED, str(exc), attempts=[attempt]
            )

        attempt = DerivativeGenerationAttempt(
            job_id=job.id,
            attempt_number=attempt_number,
            status="started",
            provider=deployment.provider,
            model_id=deployment.model_id,
            request_hash=request_hash,
            reservation_key=reservation_key,
            reserved_input_tokens=input_tokens,
            reserved_output_tokens=self._max_output_tokens,
            reserved_cost_usd=reservation.cost_usd,
        )
        self._session.add(attempt)
        await self._session.flush()

        started = time.perf_counter()
        try:
            response = await self._transport.complete(
                model=deployment.resolved_name,
                messages=compile_prompt(payload, intent=job.intent),
                timeout=self._timeout,
                num_retries=0,
                stream=False,
                max_tokens=self._max_output_tokens,
                temperature=self._temperature,
            )
        except asyncio.TimeoutError:
            self._budget_gate.release(reservation_key)
            attempt.status = "outcome_unknown"
            attempt.error_code = CODE_PROVIDER_TIMEOUT
            attempt.latency_ms = int((time.perf_counter() - started) * 1000)
            job.status = "outcome_unknown"
            job.status_reason = "provider call timed out; outcome is unknown"
            job.error_code = CODE_PROVIDER_TIMEOUT
            await self._session.flush()
            return await self._finish(
                job, None, [attempt], "outcome_unknown", CODE_PROVIDER_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001 - stable reason code required
            self._budget_gate.release(reservation_key)
            attempt.status = "outcome_unknown"
            attempt.error_code = CODE_PROVIDER_ERROR
            attempt.latency_ms = int((time.perf_counter() - started) * 1000)
            job.status = "outcome_unknown"
            job.status_reason = (f"{type(exc).__name__}: {str(exc)[:180]}")[:160]
            job.error_code = CODE_PROVIDER_ERROR
            await self._session.flush()
            return await self._finish(
                job, None, [attempt], "outcome_unknown", CODE_PROVIDER_ERROR
            )

        usage = _response_usage(response)
        response_hash = canonical_candidate_hash(response)
        latency_ms = int((time.perf_counter() - started) * 1000)
        actual_cost = self._actual_cost(deployment, usage)

        # ---- strict schema + deterministic gate (never silently repaired) ----
        try:
            draft = parse_candidate(_response_content(response))
        except (ValidationError, ValueError) as exc:
            self._budget_gate.settle(
                reservation_key,
                actual_input_tokens=usage["input_tokens"],
                actual_output_tokens=usage["output_tokens"],
                actual_cost_usd=actual_cost,
            )
            attempt.status = "failed"
            attempt.response_hash = response_hash
            attempt.provider_request_id = _response_request_id(response)
            attempt.usage = usage
            attempt.cost_usd = actual_cost
            attempt.latency_ms = latency_ms
            attempt.error_code = CODE_SCHEMA_INVALID
            job.status = "blocked"
            job.status_reason = str(exc)[:160]
            job.error_code = CODE_SCHEMA_INVALID
            job.response_hash = response_hash
            await self._session.flush()
            return await self._finish(
                job, None, [attempt], "blocked", CODE_SCHEMA_INVALID
            )

        gate: CandidateGateResult = apply_deterministic_gates(
            draft,
            payload,
            expected_package_hash=job.package_hash,
            package_intent=job.intent,
        )
        verdict = gate.verdict.value
        candidate_row = DerivativeGenerationCandidate(
            owner_id=owner_id,
            novel_id=novel_id,
            job_id=job.id,
            intent=draft.intent.value,
            draft_text=draft.draft_text,
            summary=draft.summary,
            citation_keys=list(draft.citation_keys),
            divergence=(
                draft.divergence.model_dump(mode="json")
                if draft.divergence is not None
                else None
            ),
            branch_suggestions=[
                s.model_dump(mode="json") for s in draft.branch_suggestions
            ],
            canon_delta_hash=(
                candidate_hash(draft) if draft.divergence is not None else None
            ),
            gate_verdict=verdict,
            gate_reason=gate.reason,
            package_hash=job.package_hash,
            prompt_hash=job.prompt_hash,
            schema_hash=job.schema_hash,
            request_hash=request_hash,
            response_hash=response_hash,
            usage=usage,
            cost_usd=actual_cost,
            model_lineage=dict(deployment.as_dict()),
            approval_state=(
                "needs_override" if verdict == "needs_override" else "candidate"
            ),
        )
        self._session.add(candidate_row)
        await self._session.flush()

        self._budget_gate.settle(
            reservation_key,
            actual_input_tokens=usage["input_tokens"],
            actual_output_tokens=usage["output_tokens"],
            actual_cost_usd=actual_cost,
        )
        attempt.status = "succeeded"
        attempt.response_hash = response_hash
        attempt.provider_request_id = _response_request_id(response)
        attempt.usage = usage
        attempt.cost_usd = actual_cost
        attempt.latency_ms = latency_ms
        attempt.error_code = None
        job.status = _VERDICT_TO_JOB_STATUS[gate.verdict]
        job.status_reason = gate.detail
        job.error_code = None if gate.verdict is GateVerdict.CANDIDATE else gate.reason
        job.response_hash = response_hash
        await self._session.flush()
        return await self._finish(
            job,
            candidate_row,
            [attempt],
            job.status,
            job.error_code,
            verdict,
            gate.reason,
        )

    # -- internal helpers ---------------------------------------------------

    async def _finish(
        self,
        job: DerivativeGenerationJob,
        candidate: DerivativeGenerationCandidate | None,
        attempts: list[DerivativeGenerationAttempt],
        status: str,
        error_code: str | None = None,
        gate_verdict: str | None = None,
        gate_reason: str | None = None,
    ) -> CandidateRunResult:
        """Refresh the mutated job so ``updated_at`` etc. are readable async.

        An UPDATE flush expires ``onupdate`` columns (TimestampMixin); accessing
        them from a plain sync view outside an awaited session call raises
        MissingGreenlet. Re-select the row before handing it to the wire.
        """
        await self._session.refresh(job)
        return CandidateRunResult(
            job,
            candidate,
            attempts,
            status,
            error_code,
            gate_verdict,
            gate_reason,
        )

    async def _load_scoped_job(
        self, owner_id: int, novel_id: int, job_id: int
    ) -> DerivativeGenerationJob:
        job = await self._session.scalar(
            select(DerivativeGenerationJob)
            .where(
                DerivativeGenerationJob.id == job_id,
                DerivativeGenerationJob.owner_id == owner_id,
                DerivativeGenerationJob.novel_id == novel_id,
            )
            .with_for_update()
        )
        if job is None:
            raise CandidateRunError(
                "job_not_found",
                "generation job not found in the owner/novel scope",
                status_code=404,
            )
        return job

    async def _load_scoped_package(
        self, owner_id: int, novel_id: int, job: DerivativeGenerationJob
    ) -> ContextPackageRecord | None:
        return await self._session.scalar(
            select(ContextPackageRecord).where(
                ContextPackageRecord.id == job.context_package_id,
                ContextPackageRecord.owner_id == owner_id,
                ContextPackageRecord.novel_id == novel_id,
            )
        )

    async def _next_attempt_number(self, job_id: int) -> int:
        current = await self._session.scalar(
            select(
                func.coalesce(func.max(DerivativeGenerationAttempt.attempt_number), 0)
            ).where(DerivativeGenerationAttempt.job_id == job_id)
        )
        return int(current or 0) + 1

    def _request_hash(
        self, job: DerivativeGenerationJob, deployment: ModelDeployment
    ) -> str:
        return canonical_hash(
            {
                "artifact_kind": "derivative_generation",
                "schema_version": DERIVATIVE_GENERATION_SCHEMA_VERSION,
                "job_id": job.id,
                "package_hash": job.package_hash,
                "intent": job.intent,
                "prompt_hash": job.prompt_hash,
                "schema_hash": job.schema_hash,
                "config_hash": job.config_hash,
                "deployment": deployment.lineage,
                "max_output_tokens": self._max_output_tokens,
                "timeout": self._timeout,
            }
        )

    def _actual_cost(
        self, deployment: ModelDeployment, usage: dict[str, int]
    ) -> Decimal:
        in_price = deployment.input_price_per_million or Decimal(0)
        out_price = deployment.output_price_per_million or Decimal(0)
        return (
            Decimal(usage["input_tokens"]) * in_price
            + Decimal(usage["output_tokens"]) * out_price
        ) / Decimal(1_000_000)

    async def _record_rejected_attempt(
        self,
        job: DerivativeGenerationJob,
        attempt_number: int,
        deployment: ModelDeployment,
        request_hash: str,
        error_code: str,
    ) -> DerivativeGenerationAttempt:
        attempt = DerivativeGenerationAttempt(
            job_id=job.id,
            attempt_number=attempt_number,
            status="failed",
            provider=deployment.provider,
            model_id=deployment.model_id,
            request_hash=request_hash,
            reserved_input_tokens=0,
            reserved_output_tokens=0,
            reserved_cost_usd=Decimal(0),
            usage={},
            cost_usd=Decimal(0),
            error_code=error_code,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def _pause_budget(
        self,
        job: DerivativeGenerationJob,
        error_code: str,
        detail: str,
        *,
        attempts: list[DerivativeGenerationAttempt] | None = None,
    ) -> CandidateRunResult:
        job.status = "paused_budget"
        job.status_reason = detail
        job.error_code = error_code
        await self._session.flush()
        return await self._finish(
            job, None, list(attempts or []), "paused_budget", error_code
        )

    async def _fail(
        self, job: DerivativeGenerationJob, error_code: str, detail: str
    ) -> CandidateRunResult:
        job.status = "failed"
        job.status_reason = detail
        job.error_code = error_code
        await self._session.flush()
        return await self._finish(job, None, [], "failed", error_code)


__all__ = [
    "AIServiceTransport",
    "BudgetExceeded",
    "CandidateRunError",
    "CandidateRunResult",
    "CODE_BUDGET_EXHAUSTED",
    "CODE_BUDGET_PAUSED",
    "CODE_CANCELLED",
    "CODE_INTENT_MISMATCH",
    "CODE_PACKAGE_HASH_MISMATCH",
    "CODE_PACKAGE_NOT_FOUND",
    "CODE_PROVIDER_ERROR",
    "CODE_PROVIDER_TIMEOUT",
    "CODE_SCHEMA_INVALID",
    "CODE_UNKNOWN_PRICING",
    "DEFAULT_DERIVATIVE_BUDGET",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DERIVATIVE_GENERATION_TASK",
    "DerivativeBudgetGate",
    "DerivativeBudgetPolicy",
    "DerivativeCandidateRunner",
    "ModelDeployment",
    "ModelTransport",
    "Reservation",
    "UnknownPricing",
    "canonical_hash",
    "build_generation_idempotency_key",
    "compile_prompt",
    "config_hash",
    "deployment_from_tier",
    "prompt_hash",
]
