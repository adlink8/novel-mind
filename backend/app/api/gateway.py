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
import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_gateway_token
from app.core.url_security import validate_ai_base_url
from app.models import AIModelConfig, SkillRun, SkillVersion
from app.services.ai_service import AIService, ai_service
from app.services.agent_settings_service import resolve_task_model

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
    # thinking 模型的推理内容必须随 assistant 历史回传（deepseek 等强制要求）。
    reasoning_content: str | None = Field(default=None)
    reasoning_details: Any = Field(default=None)


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
    # pi 在 model.reasoning=true 时会携带思考控制字段；上游兼容性由模型档案
    # （extra_params/extra_body）负责，网关接受但不转发，避免 422。
    reasoning_effort: str | None = Field(default=None)
    thinking: dict[str, Any] | None = Field(default=None)
    reasoning: dict[str, Any] | None = Field(default=None)
    prompt_cache_key: str | None = Field(default=None)
    prompt_cache_retention: str | None = Field(default=None)


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


@dataclass(frozen=True)
class GatewayDeployment:
    """FastAPI 权威解析后的上游连接；不会返回给 agent-service。"""

    model: str
    api_key: str | None = None
    api_base: str | None = None
    task: str | None = None
    skill_name: str | None = None
    owner_id: int | None = None
    model_config_id: int | None = None
    # 操作员在 AIModelConfig.extra_params 配置的 provider 特定参数（如
    # opencode/zen 的 {"thinking": {"type": "disabled"}}），原样透传 litellm extra_body。
    extra_params: dict | None = None
    # 模型档案默认值（AIModelConfig 列）；None 表示未配置，调用方才用内置兜底。
    default_max_tokens: int | None = None
    default_temperature: float | None = None


TASK_BY_SKILL = {
    "answer-reading-question": "qa",
    "continue-derivative-story": "continuation",
    "edit-derivative-story": "continuation",
    "illustrate-scene": "illustration",
    "illustrate-derivative-scene": "illustration",
    "propose-illustration-anchor": "illustration",
    "evaluate-reading-skill-runs": "rag_eval",
    "analyze-chapter": "deep_analysis",
    "build-story-arc": "deep_analysis",
    "detect-key-scenes": "deep_analysis",
    "propose-world-model-candidates": "deep_analysis",
    "build-visual-bible": "deep_analysis",
    "compile-scene-spec": "deep_analysis",
    "create-canon-fork": "deep_analysis",
    "prepare-export": "deep_analysis",
}

# 输出契约=结构化 JSON 的分析 skill（与 agent-service ANALYSIS_SPECS 对齐）。
# 网关在调用上游时为其强制 response_format=json_object，防止弱模型输出散文导致
# analysis-envelope 构造失败（fail-closed 前的确定性护栏）。
_JSON_CONTRACT_SKILLS = frozenset(
    {
        "analyze-chapter",
        "detect-key-scenes",
        "propose-world-model-candidates",
        "build-visual-bible",
    }
)


def _json_response_format(skill_name: str | None) -> dict[str, str] | None:
    """分析类 skill 强制 JSON 输出；其余 skill 不约束（问答是散文）。"""
    if skill_name in _JSON_CONTRACT_SKILLS:
        return {"type": "json_object"}
    return None


def _json_temperature(
    skill_name: str | None, requested: float | None, configured: float | None = None
) -> float:
    """优先级：请求显式值 > 模型档案（AIModelConfig.temperature）> 内置 0.7。

    注意：曾对 JSON 契约 skill 强制 0.0，但实测 opencode/zen 的 deepseek-v4-flash
    在 temperature=0 下长上下文最终轮退化为纯空白输出，故移除强制。
    """
    if requested is not None:
        return requested
    if configured is not None:
        return configured
    return 0.7


async def _resolve_gateway_deployment(
    requested: str,
    *,
    request: Request,
    db: AsyncSession,
) -> GatewayDeployment:
    """把 Pi 逻辑模型解析为运行 owner 保存的默认连接。

    agent-service（provider.ts）用逻辑 id `reader-chat-default` 调用网关；
    真实模型名、Base URL 与密钥由 FastAPI 按 per-run token 的 owner 决定。
    agent-service 不持有 provider key，也不能提交上游地址。
    """
    if requested and requested != "reader-chat-default":
        return GatewayDeployment(model=requested)

    run_token = request.headers.get("x-novelmind-run-token", "")
    raw_novel_id = request.headers.get("x-novelmind-novel-id", "")
    try:
        novel_id = int(raw_novel_id)
    except (TypeError, ValueError):
        novel_id = 0
    if not run_token or novel_id < 1:
        raise HTTPException(status_code=401, detail="缺少有效的 Agent 运行上下文")

    token_hash = hashlib.sha256(run_token.encode("utf-8")).hexdigest()
    run = (
        await db.execute(
            select(SkillRun).where(
                SkillRun.internal_token_hash == token_hash,
                SkillRun.novel_id == novel_id,
                SkillRun.status.in_(("queued", "running")),
            )
        )
    ).scalars().first()
    if run is None:
        raise HTTPException(status_code=401, detail="无效或已终止的 Agent 运行上下文")

    skill_name = await db.scalar(
        select(SkillVersion.name).where(SkillVersion.id == run.skill_version_id)
    )
    task = TASK_BY_SKILL.get(skill_name or "")
    configured = (
        await resolve_task_model(db, owner_id=run.owner_id, task=task)
        if task
        else None
    )

    if configured is None:
        configured = (
            await db.execute(
                select(AIModelConfig).where(
                    AIModelConfig.owner_id == run.owner_id,
                    AIModelConfig.is_active.is_(True),
                    AIModelConfig.is_default.is_(True),
                )
            )
        ).scalars().first()
    if configured is None:
        raise HTTPException(status_code=409, detail="尚未配置默认 AI 模型")

    safe_base_url = await validate_ai_base_url(configured.base_url)

    return GatewayDeployment(
        model=AIService.litellm_model_name(
            configured.provider,
            configured.model_id,
        ),
        api_key=configured.api_key,
        api_base=safe_base_url,
        task=task,
        skill_name=skill_name,
        owner_id=run.owner_id,
        model_config_id=configured.id,
        extra_params=dict(configured.extra_params or {}) or None,
        default_max_tokens=int(configured.max_tokens) if configured.max_tokens else None,
        default_temperature=(
            float(configured.temperature) if configured.temperature is not None else None
        ),
    )


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
        if m.reasoning_content is not None:
            normalized["reasoning_content"] = m.reasoning_content
        if m.reasoning_details is not None:
            normalized["reasoning_details"] = m.reasoning_details
        if m.role == "tool":
            # Pi 的 tool 消息经 content 之外的字段（tool_call_id）关联；模型层
            # 需要 tool_call_id + name 才能映射到 Gemini functionResponse。
            normalized["tool_call_id"] = getattr(m, "tool_call_id", None)
            normalized["name"] = getattr(m, "name", None)
        out.append(normalized)
    return out


async def _non_stream_completion(
    payload: GatewayChatRequest,
    deployment: GatewayDeployment,
) -> dict[str, Any]:
    """非流式：委托 AIService.chat，组装 OpenAI completion JSON。"""
    try:
        response = await ai_service.chat(
            messages=_normalize_messages(payload.messages),
            model=deployment.model,
            temperature=_json_temperature(
                deployment.skill_name, payload.temperature, deployment.default_temperature
            ),
            max_tokens=payload.max_tokens or deployment.default_max_tokens or 4096,
            stream=False,
            task_type=deployment.task or "gateway",
            tools=payload.tools,
            response_format=_json_response_format(deployment.skill_name),
            extra_body=deployment.extra_params,
            api_key=deployment.api_key,
            api_base=deployment.api_base,
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
        finish_reason = None
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            content = message.get("content", "") or ""
            tool_calls = message.get("tool_calls")
            finish_reason = choices[0].get("finish_reason")
    else:
        message = response.choices[0].message
        content = message.content or ""
        tool_calls = getattr(message, "tool_calls", None)
        finish_reason = getattr(response.choices[0], "finish_reason", None)
    # 真实 finish_reason 优先；上游缺失才按形状兜底——绝不把 length 伪装成 stop。
    if not finish_reason:
        finish_reason = "tool_calls" if tool_calls else "stop"

    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": _response_id(response),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": deployment.model,
        "model_lineage": {
            "owner_id": deployment.owner_id,
            "task": deployment.task,
            "skill_name": deployment.skill_name,
            "model": deployment.model,
            "model_config_id": deployment.model_config_id,
        },
        "choices": [
            {
                "index": 0,
                "message": msg,
                "finish_reason": finish_reason,
            }
        ],
        "usage": _usage_dict(getattr(response, "usage", None)),
    }


async def _stream_completion(
    payload: GatewayChatRequest,
    deployment: GatewayDeployment,
):
    """流式：包装 AIService.stream_chat 为 OpenAI SSE chunk，以 [DONE] 收尾。"""
    stream_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    tool_calls_sent = False
    upstream_finish: str | None = None
    try:
        async for delta in ai_service.stream_chat(
            messages=_normalize_messages(payload.messages),
            model=deployment.model,
            task_type=deployment.task or "gateway",
            tools=payload.tools,
            response_format=_json_response_format(deployment.skill_name),
            max_tokens=payload.max_tokens or deployment.default_max_tokens or 4096,
            temperature=_json_temperature(
                deployment.skill_name, payload.temperature, deployment.default_temperature
            ),
            extra_body=deployment.extra_params,
            api_key=deployment.api_key,
            api_base=deployment.api_base,
        ):
            if isinstance(delta, dict) and "__finish_reason__" in delta:
                # 上游真实 finish_reason（stop/length/content_filter/tool_calls）
                upstream_finish = str(delta["__finish_reason__"])
                continue
            if isinstance(delta, dict) and "__tool_calls__" in delta:
                # 工具调用：yield delta.tool_calls chunk（Pi openai-completions 契约）。
                chunk = {
                    "id": stream_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": deployment.model,
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
                "model": deployment.model,
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
                    "model": deployment.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": upstream_finish
                            or ("tool_calls" if tool_calls_sent else "stop"),
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
async def chat_completions(
    payload: GatewayChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """OpenAI 兼容 chat/completions；stream=true 返回 text/event-stream。"""
    deployment = await _resolve_gateway_deployment(
        payload.model,
        request=request,
        db=db,
    )
    if payload.stream:
        return StreamingResponse(
            _stream_completion(payload, deployment),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    return await _non_stream_completion(payload, deployment)


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
