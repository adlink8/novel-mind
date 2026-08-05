"""
AI 调用服务 - LiteLLM 统一封装

本模块是所有 AI API 调用的统一入口，基于 LiteLLM 实现多模型兼容。

LiteLLM 优势:
  - 统一接口调用 OpenAI / Anthropic / Ollama / 自定义模型
  - 自动处理 API 差异（消息格式、token 计数、流式响应）
  - 支持 fallback 和重试

提供的方法:
  - chat()           : 单次聊天（返回完整响应）
  - embedding()      : 文本向量化（用于 RAG 索引）
  - stream_chat()    : 流式聊天（SSE，实时输出）
  - test_connection() : 测试模型连通性

使用方式:
  from app.services.ai_service import ai_service
  response = await ai_service.chat(messages=[{"role": "user", "content": "你好"}])
"""

import logging
import os
import time

import litellm
from app.config import settings
from app.core.url_security import validate_ai_base_url

logger = logging.getLogger(__name__)


def _extract_token_usage(response) -> tuple[int, int]:
    """从响应的 usage 字段提取 (input_tokens, output_tokens)，取不到返回 (0, 0)。"""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0

    def _get(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    input_tokens = _get(usage, "prompt_tokens") or _get(usage, "input_tokens") or 0
    output_tokens = (
        _get(usage, "completion_tokens") or _get(usage, "output_tokens") or 0
    )
    return int(input_tokens), int(output_tokens)


def _provider_of(model: str) -> str:
    """从 LiteLLM 模型标识推断提供商（vertex_google/gemini-x → vertex_google）。"""
    return model.split("/", 1)[0] if "/" in (model or "") else "openai"


async def _log_usage(
    *,
    model_name: str,
    provider: str,
    task_type: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    novel_id: int | None = None,
    status: str = "success",
) -> None:
    """
    写入 AI 调用日志（ai_usage_logs）。

    安全保证: 任何失败只记 warning，绝不影响主调用。
    费用说明: 项目暂无统一按模型计价表（计价逻辑分散在各子系统的
    budget/gateway 中，按 deployment 配置），cost_usd 先记 0.0。
    """
    try:
        from app.core.database import async_session_factory
        from app.models.ai_usage_log import AIUsageLog

        async with async_session_factory() as db:
            db.add(
                AIUsageLog(
                    model_name=model_name,
                    provider=provider,
                    task_type=task_type,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=0.0,  # TODO: 接入统一计价后按 token 计算
                    latency_ms=latency_ms,
                    novel_id=novel_id,
                    status=status,
                )
            )
            await db.commit()
    except Exception as e:
        logger.warning("写入 AI 用量日志失败（已忽略）: %s", e)


def _sync_provider_env_keys() -> None:
    """把 NOVELMIND_* 密钥同步到 LiteLLM 识别的环境变量（不覆盖已有值）。"""
    if settings.gemini_api_key:
        os.environ.setdefault("GEMINI_API_KEY", settings.gemini_api_key)
        os.environ.setdefault("GOOGLE_API_KEY", settings.gemini_api_key)
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)


class AIService:
    """
    统一的 AI 调用服务。

    所有 AI 相关的业务逻辑（分析、续写、抽取）都通过本服务调用模型，
    而不是直接调用 LiteLLM。这样可以:
    1. 集中管理 API Key 和 base_url
    2. 统一记录调用日志和 token 用量
    3. 未来方便添加重试、缓存、限流等中间件
    """

    def __init__(self):
        # 默认与「数据分析」一致：Google Cloud Vertex Gemini
        self.default_model = (
            settings.default_chat_model or "vertex_google/gemini-3.5-flash-lite"
        )
        self.default_provider = settings.chat_provider or "vertex_google"
        _sync_provider_env_keys()

    def _resolve_api_key(self, model: str, api_key: str | None) -> str | None:
        if api_key:
            return api_key
        m = (model or "").lower()
        if m.startswith(("vertex_google/", "vertex_ai/", "vertex/", "gcp/")):
            return None  # Vertex 用 gcloud token，不用 API Key
        if m.startswith("gemini/") or m.startswith("google/"):
            return settings.gemini_api_key or None
        if m.startswith("claude") or m.startswith("anthropic/"):
            return settings.anthropic_api_key or None
        if m.startswith("gpt") or m.startswith("openai/") or m.startswith("o1"):
            return settings.openai_api_key or None
        return None

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float | None = None,
        task_type: str = "analysis",
        **_extra: object,
    ):
        """
        统一聊天接口（非流式）。

        Args:
            messages: 消息列表，格式 [{"role": "user/assistant/system", "content": "..."}]
            model: 模型标识（Vertex: vertex_google/gemini-3.5-flash-lite；或 LiteLLM 名）
            temperature: 生成温度（0-2，越高越随机）
            max_tokens: 最大输出 token 数
            stream: 是否流式输出（此方法建议使用 stream_chat 代替流式）
            api_key: 可选，覆盖环境变量中的密钥（来自 AIModelConfig；Vertex 忽略）
            api_base: 可选，自定义 API 地址
            task_type: 任务类型（写入用量日志，默认 "analysis"）

        Returns:
            类 OpenAI/LiteLLM 响应：response.choices[0].message.content
        """
        _sync_provider_env_keys()
        model = model or self.default_model

        from app.services.vertex_gemini import acomplete as vertex_acomplete
        from app.services.vertex_gemini import is_vertex_model

        start = time.perf_counter()
        try:
            if is_vertex_model(model) and not stream:
                response = await vertex_acomplete(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            else:
                kwargs: dict = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": stream,
                }
                resolved_key = self._resolve_api_key(model, api_key)
                if resolved_key:
                    kwargs["api_key"] = resolved_key
                if api_base:
                    kwargs["api_base"] = api_base

                response = await litellm.acompletion(**kwargs)
        except Exception:
            await _log_usage(
                model_name=model,
                provider=_provider_of(model),
                task_type=task_type,
                latency_ms=int((time.perf_counter() - start) * 1000),
                status="failed",
            )
            raise

        input_tokens, output_tokens = _extract_token_usage(response)
        await _log_usage(
            model_name=model,
            provider=_provider_of(model),
            task_type=task_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        return response

    async def embedding(
        self,
        texts: list[str],
        model: str | None = None,
        task_type: str = "embedding",
    ):
        """
        生成文本向量（embedding）。

        支持三种模式（由 settings.embedding_provider 控制）:
          - local_st: 本机 sentence-transformers + BGE（与「数据分析」相同，默认）
          - ollama:   HTTP 调 Ollama /api/embed
          - openai:   LiteLLM aembedding

        Args:
            texts: 待向量化的文本列表
            model: 嵌入模型标识（local_st 下可忽略，使用 model_path）
            task_type: 任务类型（写入用量日志，默认 "embedding"）

        Returns:
            向量列表，每个元素是 float 数组（维度由模型决定）
        """
        provider = (settings.embedding_provider or "local_st").lower()
        model = model or settings.embedding_model

        start = time.perf_counter()
        input_tokens = 0
        try:
            # 1) 本地 ST（默认，对齐数据分析）
            if provider in ("local_st", "local", "sentence_transformers", "bge"):
                from app.services.local_embed import aembed_batch

                # 允许通过环境覆盖 device（local_embed 读取）
                if getattr(settings, "embedding_device", None):
                    import os

                    os.environ.setdefault(
                        "NOVELMIND_EMBED_DEVICE", settings.embedding_device
                    )
                embeddings = await aembed_batch(
                    texts,
                    batch_size=getattr(settings, "embedding_batch_size", 64) or 64,
                    model_path=getattr(settings, "embedding_model_path", None),
                )

            # 2) Ollama HTTP
            elif provider == "ollama":
                import httpx

                model = model.replace("ollama/", "")
                embeddings = []
                async with httpx.AsyncClient(timeout=120) as client:
                    for text in texts:
                        resp = await client.post(
                            f"{settings.ollama_base_url}/api/embed",
                            json={"model": model, "input": text},
                        )
                        if resp.status_code >= 400:
                            raise RuntimeError(
                                f"Ollama embedding HTTP {resp.status_code}: {resp.text[:200]}"
                            )
                        if not resp.content:
                            raise RuntimeError(
                                "Ollama embedding 返回空响应（服务可能未启动或 502）"
                            )
                        data = resp.json()
                        emb_list = data.get("embeddings", [])
                        if emb_list and emb_list[0]:
                            embeddings.append(emb_list[0])
                        else:
                            raise RuntimeError(
                                f"Ollama embedding 返回空向量。模型 {model} 可能不支持 embedding。"
                                f" 响应: {data}"
                            )

            # 3) OpenAI / LiteLLM
            else:
                response = await litellm.aembedding(
                    model=model,
                    input=texts,
                )
                input_tokens, _ = _extract_token_usage(response)
                embeddings = [item["embedding"] for item in response.data]
        except Exception:
            await _log_usage(
                model_name=model,
                provider=_provider_of(model),
                task_type=task_type,
                latency_ms=int((time.perf_counter() - start) * 1000),
                status="failed",
            )
            raise

        await _log_usage(
            model_name=model,
            provider=_provider_of(model),
            task_type=task_type,
            input_tokens=input_tokens,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
        return embeddings

    async def stream_chat(
        self,
        messages: list[dict],
        model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        task_type: str = "analysis",
    ):
        """
        流式聊天接口（异步生成器）。

        使用方式:
          async for chunk in ai_service.stream_chat(messages):
              print(chunk, end="")

        Args:
            messages: 消息列表
            model: 模型标识
            api_key: 可选 API Key
            api_base: 可选自定义地址
            task_type: 任务类型（写入用量日志，默认 "analysis"）

        Yields:
            每次生成的文本片段（增量式）
        """
        _sync_provider_env_keys()
        model = model or self.default_model

        from app.services.vertex_gemini import acomplete as vertex_acomplete
        from app.services.vertex_gemini import is_vertex_model

        # Vertex 模型：复用自研 GCP SDK 客户端（非流式），按增量块 yield 保持
        # 流式契约。litellm 不识别 `vertex_google/` 前缀（stream 路径会抛
        # "LLM Provider NOT provided"），故不走 litellm 流式。
        if is_vertex_model(model):
            start = time.perf_counter()
            status = "success"
            try:
                response = await vertex_acomplete(messages, model=model)
                text = response.choices[0].message.content or ""
                for i in range(0, len(text), 16):
                    yield text[i : i + 16]
            except Exception:
                status = "failed"
                raise
            finally:
                await _log_usage(
                    model_name=model,
                    provider=_provider_of(model),
                    task_type=task_type,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    status=status,
                )
            return

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        resolved_key = self._resolve_api_key(model, api_key)
        if resolved_key:
            kwargs["api_key"] = resolved_key
        if api_base:
            kwargs["api_base"] = api_base

        start = time.perf_counter()
        status = "success"
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception:
            status = "failed"
            raise
        finally:
            # 流式响应通常不携带 usage，token 记 0
            await _log_usage(
                model_name=model,
                provider=_provider_of(model),
                task_type=task_type,
                latency_ms=int((time.perf_counter() - start) * 1000),
                status=status,
            )

    @staticmethod
    def litellm_model_name(provider: str, model_id: str) -> str:
        """将 DB 中的 provider + model_id 规范为调用用模型名。"""
        p = (provider or "").strip().lower()
        mid = (model_id or "").strip()
        if not mid:
            return mid
        # 已是完整标识
        if "/" in mid:
            return mid
        if p in ("", "custom"):
            return mid
        # openai 常用裸名 gpt-4o-mini
        if p == "openai" and not mid.startswith("openai/"):
            return mid
        # Vertex 统一前缀
        if p in ("vertex_google", "vertex", "vertex_ai", "gcp", "google_cloud"):
            return f"vertex_google/{mid}"
        return f"{p}/{mid}"

    async def test_connection(self, model_config) -> str:
        """
        测试模型连通性。

        发送一条简单消息，验证 API Key / gcloud token 和 base_url 是否有效。

        Args:
            model_config: AIModelConfig ORM 对象（包含 provider、model_id、api_key、base_url）

        Returns:
            模型回复文本（成功时）

        Raises:
            Exception: 连接失败时抛出异常（由调用方捕获）
        """
        _sync_provider_env_keys()
        model = self.litellm_model_name(model_config.provider, model_config.model_id)
        response = await self.chat(
            messages=[{"role": "user", "content": "Please reply with 'OK'."}],
            model=model,
            max_tokens=16,
            temperature=0,
            api_key=model_config.api_key,
            api_base=(
                await validate_ai_base_url(model_config.base_url)
                if model_config.base_url
                else None
            ),
        )
        return response.choices[0].message.content or ""


# 全局单例，整个应用共享同一个 AIService 实例
ai_service = AIService()
