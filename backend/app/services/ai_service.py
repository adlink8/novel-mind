"""AI 服务 - LiteLLM 统一封装"""

import litellm
from app.config import settings


class AIService:
    """统一的 AI 调用服务，基于 LiteLLM 支持多模型"""

    def __init__(self):
        self.default_model = "gpt-4o-mini"
        self.default_provider = "openai"

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ):
        """统一聊天接口"""
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
        """生成文本向量"""
        model = model or settings.embedding_model

        response = await litellm.aembedding(
            model=model,
            input=texts,
        )
        return [item["embedding"] for item in response.data]

    async def stream_chat(self, messages: list[dict], model: str | None = None):
        """流式聊天，返回异步生成器"""
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
        测试模型连接。
        model_config: AIModelConfig ORM 对象
        返回模型回复文本，失败抛出异常。
        """
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


# 全局单例
ai_service = AIService()
