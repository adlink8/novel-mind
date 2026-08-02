"""
OpenAI 兼容模型网关（25.2-02 / D-15）。

Pi（agent-service）通过 ``POST /api/gateway/v1/chat/completions`` 以 OpenAI
wire shape 调用 NovelMind 的模型能力。**所有 key / 路由 / 计价 / SSRF authority
都留在 AIService**（D-15）：本网关不新增 key 管理、路由表或价目表。

安全设计:
  - 整路由依赖 ``require_gateway_token``：共享环境令牌 ``NOVELMIND_GATEWAY_TOKEN``，
    fail-closed 401（缺失 / 不匹配一律拒绝）。
  - **不接收客户端自定义上游地址 / api_key**（V10 防 SSRF；请求模型 extra="forbid"，
    未知字段一律 422）。
  - 令牌不写日志、不下发浏览器；错误响应使用 OpenAI ``{"error": {...}}`` 形状。

服务到服务认证决策（RESEARCH Open Question 2，记录于 security.py）:
  (1) 网关模型调用 → 共享环境令牌（本模块）；
  (2) 工具门面调用 → 端用户 JWT（现有 require_user / require_owned_novel）；
  (3) 长时运行超过 JWT 过期的 per-run 短命令牌 → 25.2-03 handoff 项。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import require_gateway_token
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_gateway_token)])


class GatewayChatMessage(BaseModel):
    """OpenAI 消息子集；content 允许字符串。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class GatewayChatRequest(BaseModel):
    """OpenAI chat/completions 请求子集（D-15：不接受客户端自定义上游地址）。"""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1)
    messages: list[GatewayChatMessage] = Field(..., min_length=1)
    stream: bool = False
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2)


def _usage_dict(usage: Any) -> dict[str, int]:
    """从 litellm 响应 usage 提取 OpenAI usage 三元组；缺失按 0。"""
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    else:
        prompt = getattr(usage, "prompt_tokens", None) or 0
        completion = getattr(usage, "completion_tokens", None) or 0
    return {
        "prompt_tokens": int(prompt),
        "completion_tokens": int(completion),
        "total_tokens": int(prompt) + int(completion),
    }


def _response_id(response: Any) -> str:
    rid = (
        getattr(response, "id", None)
        if not isinstance(response, dict)
        else response.get("id")
    )
    return rid or f"chatcmpl-{uuid.uuid4().hex[:24]}"


async def _non_stream_completion(payload: GatewayChatRequest) -> dict[str, Any]:
    """非流式：委托 AIService.chat，组装 OpenAI completion JSON。"""
    try:
        response = await ai_service.chat(
            messages=[m.model_dump() for m in payload.messages],
            model=payload.model,
            temperature=payload.temperature if payload.temperature is not None else 0.7,
            max_tokens=payload.max_tokens or 4096,
            stream=False,
            task_type="gateway",
        )
    except Exception as exc:  # noqa: BLE001 - 统一 OpenAI 错误形状
        logger.exception("网关非流式调用失败: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "message": "upstream model call failed",
                    "type": "upstream_error",
                    "code": "upstream_error",
                }
            },
        ) from exc

    if isinstance(response, dict):
        choices = response.get("choices") or []
        content = ""
        if choices and isinstance(choices[0], dict):
            content = choices[0].get("message", {}).get("content", "") or ""
    else:
        content = response.choices[0].message.content or ""

    return {
        "id": _response_id(response),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage_dict(getattr(response, "usage", None)),
    }


async def _stream_completion(payload: GatewayChatRequest):
    """流式：包装 AIService.stream_chat 为 OpenAI SSE chunk，以 [DONE] 收尾。"""
    stream_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    try:
        async for delta in ai_service.stream_chat(
            messages=[m.model_dump() for m in payload.messages],
            model=payload.model,
            task_type="gateway",
        ):
            chunk = {
                "id": stream_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": payload.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": delta},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    except Exception as exc:  # noqa: BLE001 - 流中途失败也要以 OpenAI 错误 chunk 收尾
        logger.exception("网关流式调用失败: %s", exc)
        yield (
            "data: "
            + json.dumps(
                {
                    "error": {
                        "message": "upstream model call failed",
                        "type": "upstream_error",
                        "code": "upstream_error",
                    }
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )
    finally:
        yield "data: [DONE]\n\n"


@router.post("/v1/chat/completions")
async def chat_completions(payload: GatewayChatRequest):
    """OpenAI 兼容 chat/completions；stream=true 返回 text/event-stream。"""
    if payload.stream:
        return StreamingResponse(
            _stream_completion(payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    return await _non_stream_completion(payload)


@router.get("/v1/models")
async def list_models():
    """逻辑模型列表（当前默认模型）；key/路由 authority 仍在 AIService。"""
    model = ai_service.default_model
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "novelmind",
            }
        ],
    }
