"""Timeline worker runtime construction: transports + production runtime value.

Responsibilities of this leaf module (refactor split):
- ``TimelineWorkerRuntime`` value type (sessions/gateway/deployments/prompt/
  budget policy).
- LLM transport adapters ``_LiteLLMTransport`` / ``_VertexTransport``.
- ``_load_prompt`` (prompts/timeline_chapter_extract.v1.txt loader) and
  ``production_runtime`` which assembles the Phase 08 deployment pair
  (default Vertex Gemini, OpenAI fallback when chat_provider=openai + key).

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
            # 长篇（500+ 章）× 每章 1–2 次 Vertex 调用；预留必须覆盖 schema+证据包
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


class _VertexTransport:
    """Google Cloud Vertex structured calls（与剧情分析同一条 GCP 链路）。"""

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        from app.services.vertex_gemini import acomplete

        model = kwargs.get("model") or ""
        messages = list(kwargs.get("messages") or [])
        timeout = float(kwargs.get("timeout") or 120)
        response_format = kwargs.get("response_format")
        max_tokens = int(
            kwargs.get("max_tokens") or kwargs.get("max_output_tokens") or 4096
        )

        schema: dict[str, Any] | None = None
        if response_format is not None and hasattr(
            response_format, "model_json_schema"
        ):
            schema = response_format.model_json_schema()

        response = await acomplete(
            messages,
            model=str(model),
            temperature=0.0,
            max_tokens=max_tokens,
            timeout=timeout,
            response_json_schema=schema,
        )
        usage_obj = getattr(response, "usage", None)
        usage = {
            "input_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
            "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
        }
        content = response.choices[0].message.content or ""
        # 去掉可能的 markdown fence
        text = content.strip()
        if text.startswith("```"):
            text = (
                text.removeprefix("```json").removeprefix("```JSON").removeprefix("```")
            )
            text = text.removesuffix("```").strip()
        return {
            "id": getattr(response, "id", None) or f"vertex-{model}",
            "content": text,
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
    """Construct the production Phase 08 deployment pair.

    默认与「数据分析」/剧情分析对齐：Google Cloud Vertex Gemini。
    仅当 chat_provider 明确为 openai 且配置了 key 时回退 OpenAI。
    """
    from app.config import settings

    provider = (settings.chat_provider or "vertex_google").strip().lower()
    use_vertex = (
        provider
        in (
            "vertex_google",
            "vertex",
            "vertex_ai",
            "gcp",
            "google_cloud",
        )
        or not (settings.openai_api_key or "").strip()
    )

    if use_vertex:
        model_id = (settings.vertex_model or "gemini-3.5-flash-lite").strip()
        # Flash 级单价占位（仅预算账本用；GCP 账单以项目为准）
        deployment = ModelDeployment(
            "vertex_google",
            model_id,
            model_id,
            True,  # JSON schema via Vertex responseMimeType
            Decimal("0.10"),
            Decimal("0.40"),
        )
        return TimelineWorkerRuntime(
            sessions=async_session_factory,
            gateway=TimelineModelGateway(
                _VertexTransport(),
                persistence=PostgresCallRepository(async_session_factory),
            ),
            extraction_deployment=deployment,
            reconciliation_deployment=deployment,
            extraction_prompt=_load_prompt(),
        )

    import litellm

    extraction_model = "gpt-4o-mini-2024-07-18"
    reconciliation_model = "gpt-4o-2024-08-06"
    return TimelineWorkerRuntime(
        sessions=async_session_factory,
        gateway=TimelineModelGateway(
            _LiteLLMTransport(),
            persistence=PostgresCallRepository(async_session_factory),
        ),
        extraction_deployment=ModelDeployment(
            "openai",
            extraction_model,
            extraction_model,
            bool(
                litellm.supports_response_schema(
                    extraction_model, custom_llm_provider="openai"
                )
            ),
            Decimal("0.15"),
            Decimal("0.60"),
        ),
        reconciliation_deployment=ModelDeployment(
            "openai",
            reconciliation_model,
            reconciliation_model,
            bool(
                litellm.supports_response_schema(
                    reconciliation_model, custom_llm_provider="openai"
                )
            ),
            Decimal("2.50"),
            Decimal("10.00"),
        ),
        extraction_prompt=_load_prompt(),
    )
