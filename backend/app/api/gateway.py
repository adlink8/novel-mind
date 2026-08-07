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
    """OpenAI 消息子集；content 允许字符串或文本块数组。Pi 的 assistant 消息
    可能携带 tool_calls，tool 消息携带 tool_call_id（工具结果回传）。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: Any
    tool_calls: list[dict[str, Any]] | None = Field(default=None)
    tool_call_id: str | None = Field(default=None)
    name: str | None = Field(default=None)


class GatewayChatRequest(BaseModel):
    """OpenAI chat/completions 请求子集（D-15：不接受客户端自定义上游地址）。

    Pi openai-completions 客户端携带 OpenAI 标准补充字段（tools/tool_choice/
    stream_options/store/max_completion_tokens）。这些字段允许出现但不转发给
    上游——工具由 agent-service 侧执行，gateway 仅完成纯文本补全。
    其余字段（含 base_url/api_base，防 SSRF V10）一律 `extra="forbid"` 拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1)
    messages: list[GatewayChatMessage] = Field(..., min_length=1)
    stream: bool = False
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2)
    tools: list[dict[str, Any]] | None = Field(default=None)
    tool_choice: Any = Field(default=None)
    stream_options: dict[str, Any] | None = Field(default=None)
    store: bool | None = Field(default=None)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=32768)


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


async def _resolve_gateway_model(requested: str) -> str:
    """把 agent-service 传来的逻辑模型 id 映射到真实部署模型。

    agent-service（provider.ts）用逻辑 id `reader-chat-default` 调用网关；
    真实模型名（如 vertex_google/gemini-3.5-flash-lite）由 FastAPI 侧权威决定
    （D-15：模型路由/价目表 authority 留在 FastAPI，agent-service 零路由表）。
    若请求已带真实模型名则原样放行；`reader-chat-default` 及空值回退到
    settings.default_chat_model。解析失败时由调用方走统一 upstream_error。
    """
    if requested and requested != "reader-chat-default":
        return requested
    return ai_service.default_model


def _normalize_messages(messages: list[GatewayChatMessage]) -> list[dict[str, Any]]:
    """把 Pi 消息规范化为 ai_service 期望的 {role, content(str)} 形状。

    Pi openai-completions 可能发 content 为文本块数组或带 tool_calls 的
    assistant 消息；gateway 只消费纯文本 content（数组拼字符串）。
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m.content
        normalized: dict[str, Any] = {"role": m.role}
        if isinstance(content, str):
            normalized["content"] = content
        elif isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(part, str):
                    parts.append(part)
            normalized["content"] = "".join(parts)
        elif content is not None:
            normalized["content"] = str(content)
        if m.tool_calls is not None:
            normalized["tool_calls"] = m.tool_calls
        if m.role == "tool":
            # Pi 的 tool 消息经 content 之外的字段（tool_call_id）关联；模型层
            # 需要 tool_call_id + name 才能映射到 Gemini functionResponse。
            normalized["tool_call_id"] = getattr(m, "tool_call_id", None)
            normalized["name"] = getattr(m, "name", None)
        out.append(normalized)
    return out


async def _non_stream_completion(payload: GatewayChatRequest) -> dict[str, Any]:
    """非流式：委托 AIService.chat，组装 OpenAI completion JSON。"""
    try:
        response = await ai_service.chat(
            messages=_normalize_messages(payload.messages),
            model=await _resolve_gateway_model(payload.model),
            temperature=payload.temperature if payload.temperature is not None else 0.7,
            max_tokens=payload.max_tokens or 4096,
            stream=False,
            task_type="gateway",
            tools=payload.tools,
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
        tool_calls = None
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            content = message.get("content", "") or ""
            tool_calls = message.get("tool_calls")
    else:
        message = response.choices[0].message
        content = message.content or ""
        tool_calls = getattr(message, "tool_calls", None)

    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": _response_id(response),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": msg,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": _usage_dict(getattr(response, "usage", None)),
    }


async def _stream_completion(payload: GatewayChatRequest):
    """流式：包装 AIService.stream_chat 为 OpenAI SSE chunk，以 [DONE] 收尾。"""
    stream_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    tool_calls_sent = False
    try:
        async for delta in ai_service.stream_chat(
            messages=_normalize_messages(payload.messages),
            model=await _resolve_gateway_model(payload.model),
            task_type="gateway",
            tools=payload.tools,
        ):
            if isinstance(delta, dict) and "__tool_calls__" in delta:
                # 工具调用：yield delta.tool_calls chunk（Pi openai-completions 契约）。
                chunk = {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": payload.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": i,
                                        "id": tc.get("id"),
                                        "type": "function",
                                        "function": {
                                            "name": tc.get("function", {}).get("name"),
                                            "arguments": tc.get("function", {}).get(
                                                "arguments", "{}"
                                            ),
                                        },
                                    }
                                    for i, tc in enumerate(delta["__tool_calls__"])
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                tool_calls_sent = True
                continue
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
        # OpenAI SSE 契约：内容结束后必须有一个带 finish_reason 的终止 chunk，
        # 之后才是 [DONE]。Pi openai-completions 客户端依赖它结束流。
        yield (
            "data: "
            + json.dumps(
                {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": payload.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "tool_calls"
                            if tool_calls_sent
                            else "stop",
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )
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
