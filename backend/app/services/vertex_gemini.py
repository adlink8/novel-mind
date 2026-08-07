"""Google Cloud Vertex AI Gemini 调用（对齐「数据分析」项目）。

认证: gcloud application-default / user login 的 access token
  （``python gcloud.py auth print-access-token``），不用 AI Studio API Key。

端点:
  https://aiplatform.googleapis.com/v1/projects/{project}
    /locations/{location}/publishers/google/models/{model}:generateContent

与 数据分析/integration/scripts/knowledge/build_knowledge_units_prod.py 保持同路径。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class VertexAuthError(RuntimeError):
    """gcloud 未登录或无法取 token。"""


class VertexAPIError(RuntimeError):
    """Vertex generateContent 调用失败。"""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _resolve_https_proxy() -> str | None:
    """出站代理：配置优先，其次常见环境变量。

    国内访问 aiplatform.googleapis.com 直连常失败，需本地代理（如 7897）。
    """
    for candidate in (
        getattr(settings, "https_proxy", None),
        os.environ.get("NOVELMIND_HTTPS_PROXY"),
        os.environ.get("HTTPS_PROXY"),
        os.environ.get("https_proxy"),
        os.environ.get("HTTP_PROXY"),
        os.environ.get("http_proxy"),
        os.environ.get("ALL_PROXY"),
        os.environ.get("all_proxy"),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return None


def _httpx_client(timeout: float) -> httpx.AsyncClient:
    """构造访问 Google API 的 AsyncClient（正确带 proxy）。"""
    proxy = _resolve_https_proxy()
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        # trust_env=True 时也会读环境变量；显式 proxy 更稳
        "trust_env": True,
    }
    if proxy:
        kwargs["proxy"] = proxy
        logger.debug("Vertex HTTP client using proxy=%s", proxy)
    return httpx.AsyncClient(**kwargs)


class GcloudTokenProvider:
    """缓存 gcloud access token（线程安全，约 50 分钟刷新）。"""

    def __init__(
        self,
        *,
        sdk_py: str | None = None,
        cloud_sdk_root: str | None = None,
    ) -> None:
        self._sdk_py = sdk_py or settings.gcp_sdk_py
        self._cloud_sdk_root = cloud_sdk_root or settings.gcp_sdk_root
        self._token: str | None = None
        self._expires: float = 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires:
                return self._token
            self._token = self._fetch()
            self._expires = time.time() + 3000  # ~50 min
            return self._token

    def refresh(self) -> str:
        with self._lock:
            self._token = None
            self._expires = 0.0
        return self.get()

    def _fetch(self) -> str:
        env = dict(os.environ)
        if self._cloud_sdk_root:
            env["CLOUDSDK_ROOT_DIR"] = self._cloud_sdk_root
        env["CLOUDSDK_CORE_DISABLE_PROMPTS"] = "1"

        if self._sdk_py and os.path.exists(self._sdk_py):
            r = subprocess.run(
                [sys.executable, self._sdk_py, "auth", "print-access-token"],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
            err = (r.stderr or r.stdout or "").strip()
            logger.warning("gcloud.py token failed: %s", err[:200])

        r = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if r.returncode != 0 or not r.stdout.strip():
            raise VertexAuthError(
                "获取 gcloud token 失败：请先 `gcloud auth login` 并确认 Vertex AI 已启用。"
                f" detail={(r.stderr or r.stdout or '')[:200]}"
            )
        return r.stdout.strip()


_token_provider = GcloudTokenProvider()


def _strip_model_prefix(model: str) -> str:
    """vertex_google/gemini-3.5-flash-lite → gemini-3.5-flash-lite。"""
    m = (model or "").strip()
    for prefix in (
        "vertex_google/",
        "vertex_ai/",
        "vertex/",
        "gcp/",
        "google/",
    ):
        if m.lower().startswith(prefix):
            return m[len(prefix) :]
    return m


def is_vertex_model(model: str | None) -> bool:
    """是否走 Vertex（含全局 chat_provider 默认）。"""
    m = (model or "").strip().lower()
    if m.startswith(("vertex_google/", "vertex_ai/", "vertex/", "gcp/")):
        return True
    provider = (settings.chat_provider or "").strip().lower()
    if provider in ("vertex_google", "vertex", "vertex_ai", "gcp", "google_cloud"):
        # 裸 gemini-* 在 vertex 默认 provider 下也走 GCP
        if not m or m.startswith("gemini") or "/" not in m:
            return True
        # 显式 openai/anthropic/ollama 不劫持
        if m.startswith(("openai/", "anthropic/", "ollama/", "gpt-", "claude")):
            return False
        if m.startswith("gemini/"):
            # gemini/* 默认是 AI Studio；仅当 chat_provider 强制 vertex 且未指定 api key 路径时
            # 这里：用户明确 gemini/ 前缀 → 仍 AI Studio（保留兼容）
            return False
    return False


def _convert_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    """OpenAI 工具定义 → Gemini functionDeclarations。

    每个 OpenAI tool 形如 {"type":"function","function":{"name","description",
    "parameters"}}，转为 Gemini {"functionDeclarations":[{"name","description",
    "parameters"}]}。无工具或空列表返回 None（不携带 tools 字段）。
    """
    if not tools:
        return None
    decls = []
    for tool in tools:
        fn = (tool or {}).get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        decl = {"name": name, "description": fn.get("description") or ""}
        params = fn.get("parameters")
        if params:
            decl["parameters"] = params
        decls.append(decl)
    if not decls:
        return None
    return [{"functionDeclarations": decls}]


# 进程内缓存：Gemini functionCall `id` → `thoughtSignature`（回传时需附带）。
# 有界（dict 天然去重；会话级 tool 调用量小）。
_thought_signatures: dict[str, str] = {}


def _messages_to_vertex_contents(
    messages: list[dict],
) -> tuple[str | None, list[dict]]:
    """OpenAI-style messages → (system_instruction, contents)。

    支持 tool 调用回传（Gemini 规则）：
      - assistant 消息带 `tool_calls` → 独立 model turn，只含 functionCall parts；
      - `role:"tool"` 消息 → 独立 function role turn，只含 functionResponse part。
      Gemini 禁止 function_call 与 function_response 混在同一 turn，也要求
      functionResponse 的 name 与先前 functionCall 一致。
    """
    system_parts: list[str] = []
    contents: list[dict] = []
    # tool_call_id → function name 映射（assistant tool_calls 在前，tool 结果在后）。
    pending_calls: dict[str, str] = {}
    for msg in messages:
        role = (msg.get("role") or "user").lower()
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        if role == "system":
            system_parts.append(str(content))
            continue
        if role == "tool":
            # 独立 function role turn，只含 functionResponse。
            name = (
                msg.get("name")
                or pending_calls.get(msg.get("tool_call_id"))
                or "unknown_tool"
            )
            contents.append(
                {
                    "role": "function",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": {"result": str(content)},
                            }
                        }
                    ],
                }
            )
            continue
        vrole = "model" if role == "assistant" else "user"
        parts: list[dict[str, Any]] = []
        if role == "assistant":
            # 独立 model turn，只含 functionCall（不与文本混）。
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                args_raw = fn.get("arguments") or "{}"
                try:
                    args = (
                        json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    )
                except json.JSONDecodeError:
                    args = {}
                fc_part: dict[str, Any] = {"functionCall": {"name": name, "args": args}}
                # Gemini 新要求：functionCall part 必须带 thought_signature。
                # tool_call_id 即首次响应的 Gemini functionCall.id，查缓存附加。
                sig = _thought_signatures.get(tc.get("id"))
                if sig:
                    fc_part["thoughtSignature"] = sig
                parts.append(fc_part)
                if tc.get("id"):
                    pending_calls[tc["id"]] = name
            if not parts:
                parts.append({"text": str(content)})
        else:
            parts.append({"text": str(content)})
        if parts:
            contents.append({"role": vrole, "parts": parts})
    system = "\n\n".join(system_parts) if system_parts else None
    if not contents:
        contents = [{"role": "user", "parts": [{"text": ""}]}]
    return system, contents


def _extract_function_calls(candidate: dict) -> list[dict] | None:
    """从 Gemini candidate parts 提取 functionCall → OpenAI tool_calls 格式。

    OpenAI 格式：{"id","type":"function","function":{"name","arguments(JSON str)"}}。
    无 functionCall 返回 None。
    同时把 Gemini 返回的 functionCall `id` + `thoughtSignature` 记入进程内缓存，
    供后续 tool 结果回传时给 functionCall part 附加 thoughtSignature
    （Gemini 新要求，见 `_messages_to_vertex_contents`）。
    """
    parts = candidate.get("content", {}).get("parts", []) or []
    calls = []
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            continue
        fc = p.get("functionCall")
        if not isinstance(fc, dict):
            continue
        call_id = fc.get("id") or f"call_{i}"
        sig = p.get("thoughtSignature")
        if sig:
            _thought_signatures[call_id] = sig
        calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": fc.get("name") or "",
                    "arguments": json.dumps(fc.get("args") or {}, ensure_ascii=False),
                },
            }
        )
    return calls or None


def _extract_text(candidate: dict) -> str:
    """兼容 thinking 模型：跳过 thought 段。"""
    parts = candidate.get("content", {}).get("parts", []) or []
    texts = [
        p.get("text", "")
        for p in parts
        if isinstance(p, dict) and p.get("text") and not p.get("thought")
    ]
    if texts:
        return "".join(texts)
    c = candidate.get("content")
    return c if isinstance(c, str) else ""


def _to_openai_like_response(
    text: str,
    usage: dict[str, Any],
    model: str,
    tool_calls: list[dict] | None = None,
) -> Any:
    """构造与 LiteLLM 兼容的响应对象（choices/message/usage）。

    tool_calls 非空时 message.tool_calls 携带 OpenAI 格式工具调用
    （Pi openai-completions 契约）。
    """
    prompt_tokens = usage.get("promptTokenCount") or usage.get("prompt_tokens")
    completion_tokens = usage.get("candidatesTokenCount") or usage.get(
        "completion_tokens"
    )
    total = usage.get("totalTokenCount") or usage.get("total_tokens")
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=text or "",
                    role="assistant",
                    tool_calls=tool_calls,
                ),
                finish_reason="tool_calls" if tool_calls else "stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
        ),
        model=model,
        _vertex=True,
    )


def _vertex_json_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """把 JSON Schema（含 Pydantic $defs）收成 Vertex responseSchema 可接受的子集。"""
    if not schema:
        return None
    defs = dict(schema.get("$defs") or schema.get("definitions") or {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            ref = str(node["$ref"]).rsplit("/", 1)[-1]
            if ref in defs:
                return resolve(defs[ref])
            return {"type": "OBJECT"}
        # Optional[T] → anyOf [T, null]：Vertex 更吃非 null 分支
        if "anyOf" in node or "oneOf" in node:
            variants = node.get("anyOf") or node.get("oneOf") or []
            non_null = [
                v
                for v in variants
                if not (isinstance(v, dict) and v.get("type") == "null")
            ]
            if len(non_null) == 1:
                merged = resolve(non_null[0])
                if isinstance(merged, dict) and node.get("description"):
                    merged = {**merged, "description": node["description"]}
                return merged
            return {
                "type": "OBJECT",
                "description": node.get("description", "union"),
            }
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in (
                "$defs",
                "definitions",
                "title",
                "default",
                "examples",
                "$schema",
            ):
                continue
            if key == "type":
                # Vertex 偏好大写 TYPE（兼容小写）
                if isinstance(value, str):
                    mapping = {
                        "object": "OBJECT",
                        "array": "ARRAY",
                        "string": "STRING",
                        "integer": "INTEGER",
                        "number": "NUMBER",
                        "boolean": "BOOLEAN",
                        "null": "STRING",
                    }
                    out[key] = mapping.get(value.lower(), value.upper())
                elif isinstance(value, list):
                    # type: ["string","null"]
                    non_null = [t for t in value if t != "null"]
                    t = non_null[0] if non_null else "string"
                    out[key] = {
                        "object": "OBJECT",
                        "array": "ARRAY",
                        "string": "STRING",
                        "integer": "INTEGER",
                        "number": "NUMBER",
                        "boolean": "BOOLEAN",
                    }.get(str(t).lower(), "STRING")
                else:
                    out[key] = value
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {pk: resolve(pv) for pk, pv in value.items()}
                continue
            if key == "items":
                out[key] = resolve(value)
                continue
            # Pydantic tuple → prefixItems; Vertex requires items with a type.
            if key == "prefixItems" and isinstance(value, list) and value:
                # Homogeneous tuple of strings/enums → single items schema.
                resolved_parts = [resolve(v) for v in value]
                first = resolved_parts[0] if resolved_parts else {"type": "STRING"}
                out["items"] = first if isinstance(first, dict) else {"type": "STRING"}
                out["type"] = "ARRAY"
                continue
            if key == "enum" and isinstance(value, list):
                out[key] = value
                continue
            if key in (
                "required",
                "description",
                "format",
                "pattern",
                "minimum",
                "maximum",
                "minItems",
                "maxItems",
                "minLength",
                "maxLength",
                "additionalProperties",
            ):
                out[key] = resolve(value) if isinstance(value, (dict, list)) else value
                continue
            # 丢弃 Vertex 不稳的高级关键字
        if "type" not in out and "properties" in out:
            out["type"] = "OBJECT"
        if "type" not in out and "items" in out:
            out["type"] = "ARRAY"
        # Vertex rejects ARRAY without items (e.g. leftover min/max only).
        if out.get("type") in ("ARRAY", "array") and "items" not in out:
            out["items"] = {"type": "STRING"}
        return out

    root = {k: v for k, v in schema.items() if k not in ("$defs", "definitions")}
    return resolve(root)


async def acomplete(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    project: str | None = None,
    location: str | None = None,
    timeout: float = 120.0,
    response_json_schema: dict[str, Any] | None = None,
    tools: list[dict] | None = None,
) -> Any:
    """异步调用 Vertex generateContent，返回 OpenAI-like 响应。

    response_json_schema: 可选 JSON Schema，启用 application/json 结构化输出
    （供时间线 Timeline gateway 使用）。
    tools: OpenAI 风格工具定义列表；转成 Gemini functionDeclarations，响应中
    的 functionCall 以 OpenAI tool_calls 形式返回。
    """
    project = project or settings.gcp_project
    location = location or settings.gcp_location
    model_id = _strip_model_prefix(model or settings.vertex_model)
    if not project:
        raise VertexAuthError("未配置 NOVELMIND_GCP_PROJECT")
    if not model_id:
        raise VertexAPIError("未指定 Vertex 模型")

    system, contents = _messages_to_vertex_contents(messages)
    url = (
        f"https://aiplatform.googleapis.com/v1/projects/{project}"
        f"/locations/{location}/publishers/google/models/{model_id}:generateContent"
    )
    generation_config: dict[str, Any] = {
        "maxOutputTokens": max_tokens,
        "temperature": temperature,
        # 与数据分析一致：关闭 thinking 预算，加快且省配额
        "thinkingConfig": {"thinkingBudget": 0},
    }
    vertex_schema = _vertex_json_schema(response_json_schema)
    if vertex_schema is not None:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = vertex_schema
        # 双保险：提示里也带 schema 摘要，降低跑偏率
        schema_hint = (
            "\n\nYou MUST respond with a single JSON object matching this schema "
            "(no markdown fences):\n"
            + json.dumps(response_json_schema, ensure_ascii=False)[:8000]
        )
        system = (system or "") + schema_hint

    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    vertex_tools = _convert_openai_tools(tools)
    if vertex_tools:
        body["tools"] = vertex_tools

    last_err: Exception | None = None
    for attempt in range(3):
        token = await asyncio.to_thread(_token_provider.get)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            async with _httpx_client(timeout) as client:
                resp = await client.post(url, headers=headers, content=json.dumps(body))
            if resp.status_code == 401 and attempt < 2:
                await asyncio.to_thread(_token_provider.refresh)
                continue
            if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise VertexAPIError(
                    f"Vertex HTTP {resp.status_code}: {resp.text[:400]}",
                    status_code=resp.status_code,
                )
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise VertexAPIError(f"Vertex 无 candidates: {str(data)[:300]}")
            text = _extract_text(candidates[0])
            tool_calls = _extract_function_calls(candidates[0])
            usage = data.get("usageMetadata") or {}
            return _to_openai_like_response(
                text, usage, model_id, tool_calls=tool_calls
            )
        except VertexAPIError as e:
            last_err = e
            if e.status_code in (429, 500, 502, 503) and attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            raise VertexAPIError(f"{type(e).__name__}: {e}") from e
    raise VertexAPIError(f"max retries exceeded: {last_err}")
