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

import litellm
from app.config import settings


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
        self.default_model = "gpt-4o-mini"       # 默认模型（未指定时使用）
        self.default_provider = "openai"          # 默认提供商

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ):
        """
        统一聊天接口（非流式）。

        Args:
            messages: 消息列表，格式 [{"role": "user/assistant/system", "content": "..."}]
            model: LiteLLM 模型标识（如 "gpt-4o"、"ollama/qwen2:7b"），为 None 使用默认模型
            temperature: 生成温度（0-2，越高越随机）
            max_tokens: 最大输出 token 数
            stream: 是否流式输出（此方法建议使用 stream_chat 代替流式）

        Returns:
            LiteLLM 响应对象，通过 response.choices[0].message.content 获取文本
        """
        model = model or self.default_model

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )
        return response

    async def embedding(self, texts: list[str], model: str | None = None):
        """
        生成文本向量（embedding）。

        Args:
            texts: 待向量化的文本列表
            model: 嵌入模型标识（默认使用 settings.embedding_model）

        Returns:
            向量列表，每个元素是 float 数组（维度由模型决定）
        """
        model = model or settings.embedding_model

        response = await litellm.aembedding(
            model=model,
            input=texts,
        )
        return [item["embedding"] for item in response.data]

    async def stream_chat(self, messages: list[dict], model: str | None = None):
        """
        流式聊天接口（异步生成器）。

        使用方式:
          async for chunk in ai_service.stream_chat(messages):
              print(chunk, end="")

        Args:
            messages: 消息列表
            model: 模型标识

        Yields:
            每次生成的文本片段（增量式）
        """
        model = model or self.default_model

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            stream=True,
        )
        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    async def test_connection(self, model_config) -> str:
        """
        测试模型连通性。

        发送一条简单消息，验证 API Key 和 base_url 是否有效。

        Args:
            model_config: AIModelConfig ORM 对象（包含 provider、model_id、api_key、base_url）

        Returns:
            模型回复文本（成功时）

        Raises:
            Exception: 连接失败时抛出异常（由调用方捕获）
        """
        # 构建 LiteLLM 参数
        kwargs = {
            "model": f"{model_config.provider}/{model_config.model_id}",
            "messages": [{"role": "user", "content": "Please reply with 'OK'."}],
            "max_tokens": 10,
        }
        if model_config.api_key:
            kwargs["api_key"] = model_config.api_key
        if model_config.base_url:
            kwargs["api_base"] = model_config.base_url

        response = await litellm.acompletion(**kwargs)
        return response.choices[0].message.content or ""


# 全局单例，整个应用共享同一个 AIService 实例
ai_service = AIService()
