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
    DependencyPaused,
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
    # 按章并发提取路数；章节间零依赖，预算/审计由 PG 行锁串行化兜底
    chapter_concurrency: int = 4


class _LiteLLMTransport:
    """统一走流式：整章提取的单次生成可达 4 分钟+，非流式请求会被
    zen 网关在 ~240s 处稳定掐断（实测 500）；持续字节流不受其影响。"""

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        import litellm

        response = await litellm.acompletion(
            **{**kwargs, "stream": True, "stream_options": {"include_usage": True}}
        )
        if not hasattr(response, "__aiter__"):
            # 供应商忽略 stream 参数时的兜底：按完整响应处理
            usage = getattr(response, "usage", {})
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump()
            message = response.choices[0].message
            return {
                "id": getattr(response, "id", None),
                "content": message.content,
                "usage": usage,
            }
        content_parts: list[str] = []
        usage: Any = None
        response_id = None
        async for chunk in response:
            response_id = response_id or getattr(chunk, "id", None)
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            choices = getattr(chunk, "choices", None)
            if choices and getattr(choices[0].delta, "content", None):
                content_parts.append(choices[0].delta.content)
        usage_dict: Any = {}
        if usage is not None:
            usage_dict = (
                usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
            )
        return {
            "id": response_id,
            "content": "".join(content_parts),
            "usage": usage_dict,
        }


def _load_prompt() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "prompts"
        / "timeline_chapter_extract.v1.txt"
    )
    return path.read_text(encoding="utf-8")


async def production_runtime(
    owner_id: int | None = None,
) -> TimelineWorkerRuntime:
    """组装部署对：优先 owner 在前端配置的默认模型，未绑定时回落 .env。

    对应 config.py 的既定语义：全局回退仅用于尚未绑定 owner 模型配置的
    后台任务。owner 配置的凭据只挂在部署对象上，由网关传入传输层 kwargs。
    """
    from app.config import settings

    import litellm

    if owner_id is not None:
        deployment = await _resolve_owner_deployment(owner_id)
    else:
        deployment = _env_fallback_deployment(settings, litellm)
    return TimelineWorkerRuntime(
        sessions=async_session_factory,
        gateway=TimelineModelGateway(
            _LiteLLMTransport(),
            persistence=PostgresCallRepository(async_session_factory),
        ),
        extraction_deployment=deployment,
        reconciliation_deployment=deployment,
        extraction_prompt=_load_prompt(),
        chapter_concurrency=max(1, settings.analysis_chapter_concurrency),
    )


async def _resolve_owner_deployment(owner_id: int) -> ModelDeployment:
    """解析 owner 默认模型配置。

    配置缺失或不合法时抛 timeline 的 DependencyPaused（带 reader 侧原因码），
    让 run 带着明确原因诚实暂停——静默回落 env 会重演占位符 key 鉴权失败的
    糊涂账。仅 owner_id=None（无 run 上下文，如 budget_policy 查询）走 env 回退。
    """
    from app.config import settings
    from app.services.ai_service import AIService
    from app.services.reader_chat.gateway import (
        DependencyPaused as ReaderDependencyPaused,
    )
    from app.services.reader_chat.worker import resolve_reader_chat_deployment

    try:
        owned = await resolve_reader_chat_deployment(owner_id=owner_id)
    except ReaderDependencyPaused as exc:
        raise DependencyPaused(str(exc)) from exc
    # custom 等 provider 映射为 litellm 认识的协议名（custom→openai 兼容）
    litellm_name = AIService.litellm_model_name(owned.provider, owned.model_id)
    provider, _, model_id = litellm_name.partition("/")
    return ModelDeployment(
        provider=provider or owned.provider,
        model_id=model_id or owned.model_id,
        revision=owned.revision,
        supports_structured_output=True,
        input_price_per_million=settings.analysis_input_price_per_million,
        output_price_per_million=settings.analysis_output_price_per_million,
        api_key=owned.api_key,
        base_url=owned.base_url,
    )


def _env_fallback_deployment(settings: Any, litellm: Any) -> ModelDeployment:
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
    ) or bool(settings.analysis_force_structured_output)
    return ModelDeployment(
        provider,
        model_id,
        model_id,
        supports_schema,
        settings.analysis_input_price_per_million,
        settings.analysis_output_price_per_million,
    )
