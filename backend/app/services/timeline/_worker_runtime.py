"""Timeline worker runtime construction: transports + production runtime value.

Responsibilities of this leaf module (refactor split):
- ``TimelineWorkerRuntime`` value type (sessions/gateway/deployments/prompt/
  budget policy).
- LLM transport adapter ``_LiteLLMTransport``.
- ``_load_prompt`` (prompts/timeline_chapter_extract.v1.txt loader) and
  ``production_runtime`` which assembles the Phase 08 deployment pair
  from the configured LiteLLM provider/model.

This module depends only on model_gateway/budget/config — it never imports
the worker facade, so no import cycle. Public names are re-exported from
``worker.py`` unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.services.timeline.budget import BudgetPolicy
from app.services.timeline.model_gateway import (
    ModelDeployment,
    PostgresCallRepository,
    TimelineModelGateway,
)


@dataclass(frozen=True)
class TimelineWorkerRuntime:
    sessions: async_sessionmaker[AsyncSession]
    gateway: TimelineModelGateway
    extraction_deployment: ModelDeployment
    reconciliation_deployment: ModelDeployment
    extraction_prompt: str = (
        "Extract only evidence-backed timeline events from the supplied package."
    )
    budget_policy: BudgetPolicy = field(
        default_factory=lambda: BudgetPolicy(
            # 长篇（500+ 章）× 每章 1–2 次模型调用；预留必须覆盖 schema+证据包
            max_calls=5_000,
            max_input_tokens=100_000_000,
            max_output_tokens=20_000_000,
            max_cost_usd=Decimal("200"),
        )
    )


class _LiteLLMTransport:
    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        import litellm

        response = await litellm.acompletion(**kwargs)
        usage = getattr(response, "usage", {})
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        message = response.choices[0].message
        return {
            "id": getattr(response, "id", None),
            "content": message.content,
            "usage": usage,
        }


def _load_prompt() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "prompts"
        / "timeline_chapter_extract.v1.txt"
    )
    return path.read_text(encoding="utf-8")


def production_runtime() -> TimelineWorkerRuntime:
    """Construct the production deployment pair from generic LiteLLM settings."""
    from app.config import settings

    import litellm

    provider = (settings.chat_provider or "openai").strip().lower()
    if provider not in {"openai", "anthropic", "gemini", "ollama", "custom"}:
        raise ValueError(f"unsupported model provider: {provider}")
    model_id = (settings.default_chat_model or "gpt-4o-mini").strip()
    if provider == "custom":
        provider = "openai"
    prefix = f"{provider}/"
    if model_id.lower().startswith(prefix):
        model_id = model_id[len(prefix) :]
    supports_schema = bool(
        litellm.supports_response_schema(model_id, custom_llm_provider=provider)
    )
    deployment = ModelDeployment(
        provider,
        model_id,
        model_id,
        supports_schema,
        Decimal("0.15"),
        Decimal("0.60"),
    )
    return TimelineWorkerRuntime(
        sessions=async_session_factory,
        gateway=TimelineModelGateway(
            _LiteLLMTransport(),
            persistence=PostgresCallRepository(async_session_factory),
        ),
        extraction_deployment=deployment,
        reconciliation_deployment=deployment,
        extraction_prompt=_load_prompt(),
    )
