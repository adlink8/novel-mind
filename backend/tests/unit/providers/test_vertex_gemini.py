"""Vertex Gemini provider adapter unit tests.

Covers the pure helper functions, the token provider (subprocess-mocked) and
``acomplete`` retry/error semantics with a mocked HTTP client. No external
network or gcloud is ever touched.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest

import app.services.vertex_gemini as vg

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Proxy resolution
# ---------------------------------------------------------------------------


def test_resolve_https_proxy_prefers_settings_then_env(monkeypatch):
    monkeypatch.setattr(vg.settings, "https_proxy", "http://cfg:7890")
    monkeypatch.delenv("NOVELMIND_HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    assert vg._resolve_https_proxy() == "http://cfg:7890"


def test_resolve_https_proxy_reads_env_variables(monkeypatch):
    monkeypatch.setattr(vg.settings, "https_proxy", "")
    monkeypatch.setenv("NOVELMIND_HTTPS_PROXY", "http://env:7891")
    assert vg._resolve_https_proxy() == "http://env:7891"


def test_resolve_https_proxy_skips_blank_values(monkeypatch):
    monkeypatch.setattr(vg.settings, "https_proxy", "   ")
    monkeypatch.setenv("HTTPS_PROXY", "http://fallback:7892")
    assert vg._resolve_https_proxy() == "http://fallback:7892"


def test_resolve_https_proxy_none_when_all_missing(monkeypatch):
    monkeypatch.setattr(vg.settings, "https_proxy", "")
    for name in (
        "NOVELMIND_HTTPS_PROXY",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    assert vg._resolve_https_proxy() is None


def test_httpx_client_carries_proxy_when_resolved(monkeypatch):
    monkeypatch.setattr(vg, "_resolve_https_proxy", lambda: "http://proxy:7890")
    with mock.patch("app.services.vertex_gemini.httpx.AsyncClient") as client_cls:
        vg._httpx_client(30.0)
        kwargs = client_cls.call_args.kwargs
        assert kwargs["timeout"] == 30.0
        assert kwargs["proxy"] == "http://proxy:7890"
        assert kwargs["trust_env"] is True


def test_httpx_client_without_proxy(monkeypatch):
    monkeypatch.setattr(vg, "_resolve_https_proxy", lambda: None)
    with mock.patch("app.services.vertex_gemini.httpx.AsyncClient") as client_cls:
        vg._httpx_client(15.0)
        kwargs = client_cls.call_args.kwargs
        assert "proxy" not in kwargs
        assert kwargs["timeout"] == 15.0


# ---------------------------------------------------------------------------
# Token provider
# ---------------------------------------------------------------------------


def test_gcloud_token_provider_cache_and_refresh():
    provider = vg.GcloudTokenProvider(
        sdk_py="/nonexistent/gcloud.py", cloud_sdk_root=None
    )
    with mock.patch.object(
        provider, "_fetch", return_value="tok-1"
    ) as fetch, mock.patch.object(vg.time, "time", return_value=1_000.0):
        assert provider.get() == "tok-1"
        assert provider.get() == "tok-1"  # cached
        assert fetch.call_count == 1
        # refresh clears the cache and re-fetches
        with mock.patch.object(vg.time, "time", return_value=100_000.0):
            assert provider.refresh() == "tok-1"
            assert fetch.call_count == 2


def test_token_provider_uses_sdk_py_first(monkeypatch):
    monkeypatch.setattr(vg.settings, "gcp_sdk_py", "")
    monkeypatch.setattr(vg.settings, "gcp_sdk_root", "")
    provider = vg.GcloudTokenProvider(sdk_py=None, cloud_sdk_root=None)
    sdk = mock.Mock()
    sdk.returncode = 0
    sdk.stdout = "sdk-token\n"
    gcloud = mock.Mock()
    gcloud.returncode = 0
    gcloud.stdout = "cli-token\n"
    with mock.patch.object(
        vg.subprocess, "run", side_effect=[sdk, gcloud]
    ) as run, mock.patch.object(
        vg.os.path, "exists", return_value=True
    ):
        token = provider._fetch()
    assert token == "sdk-token"
    assert run.call_count == 1
    env = run.call_args.kwargs["env"]
    assert env["CLOUDSDK_CORE_DISABLE_PROMPTS"] == "1"


def test_token_provider_falls_back_to_cli_when_sdk_fails():
    provider = vg.GcloudTokenProvider(sdk_py="/missing.py", cloud_sdk_root=None)
    sdk = mock.Mock()
    sdk.returncode = 1
    sdk.stdout = ""
    sdk.stderr = "auth error"
    gcloud = mock.Mock()
    gcloud.returncode = 0
    gcloud.stdout = "cli-token\n"
    with mock.patch.object(
        vg.subprocess, "run", side_effect=[sdk, gcloud]
    ), mock.patch.object(vg.os.path, "exists", return_value=True):
        token = provider._fetch()
    assert token == "cli-token"


def test_token_provider_raises_when_cli_fails():
    provider = vg.GcloudTokenProvider(sdk_py=None, cloud_sdk_root=None)
    cli = mock.Mock()
    cli.returncode = 1
    cli.stdout = ""
    cli.stderr = "login required"
    with mock.patch.object(vg.subprocess, "run", return_value=cli):
        with pytest.raises(vg.VertexAuthError, match="获取 gcloud token 失败"):
            provider._fetch()


def test_token_provider_sets_cloud_sdk_root_env():
    provider = vg.GcloudTokenProvider(sdk_py="/none", cloud_sdk_root="/opt/gcloud")
    cli = mock.Mock()
    cli.returncode = 0
    cli.stdout = "tok\n"
    with mock.patch.object(
        vg.subprocess, "run", return_value=cli
    ) as run, mock.patch.object(vg.os.path, "exists", return_value=False):
        provider._fetch()
    env = run.call_args.kwargs["env"]
    assert env["CLOUDSDK_ROOT_DIR"] == "/opt/gcloud"


# ---------------------------------------------------------------------------
# Model name helpers
# ---------------------------------------------------------------------------


def test_strip_model_prefix():
    assert vg._strip_model_prefix("vertex_google/gemini-3.5-flash-lite") == (
        "gemini-3.5-flash-lite"
    )
    assert vg._strip_model_prefix("vertex_ai/x") == "x"
    assert vg._strip_model_prefix("vertex/y") == "y"
    assert vg._strip_model_prefix("gcp/z") == "z"
    assert vg._strip_model_prefix("google/w") == "w"
    assert vg._strip_model_prefix("gemini-2") == "gemini-2"
    assert vg._strip_model_prefix("") == ""


def test_is_vertex_model_recognizes_prefixes(monkeypatch):
    monkeypatch.setattr(vg.settings, "chat_provider", "gemini")
    for model in ("vertex_google/x", "vertex_ai/x", "vertex/x", "gcp/x"):
        assert vg.is_vertex_model(model) is True


def test_is_vertex_model_default_provider_bare_gemini(monkeypatch):
    monkeypatch.setattr(vg.settings, "chat_provider", "vertex_google")
    assert vg.is_vertex_model("gemini-3.5-flash") is True
    assert vg.is_vertex_model(None) is True
    assert vg.is_vertex_model("") is True


def test_is_vertex_model_does_not_hijack_explicit_other_providers(monkeypatch):
    monkeypatch.setattr(vg.settings, "chat_provider", "vertex_google")
    # slash-qualified foreign providers are never hijacked
    for model in ("openai/gpt-4", "anthropic/claude-3", "ollama/qwen"):
        assert vg.is_vertex_model(model) is False
    # gemini/* models also route to vertex under a vertex_google provider
    assert vg.is_vertex_model("gemini/gemini-2") is True
    # bare foreign model names without a slash fall through to the vertex default
    assert vg.is_vertex_model("gpt-4") is True
    assert vg.is_vertex_model("claude-2") is True


def test_is_vertex_model_non_vertex_provider(monkeypatch):
    monkeypatch.setattr(vg.settings, "chat_provider", "openai")
    assert vg.is_vertex_model("gemini-3") is False
    assert vg.is_vertex_model("vertex_google/x") is True  # explicit prefix wins


# ---------------------------------------------------------------------------
# Tool conversion
# ---------------------------------------------------------------------------


def test_convert_openai_tools():
    tools = [
        {
            "type": "function",
            "function": {"name": "lookup", "description": "d", "parameters": {"type": "object"}},
        },
        {"type": "function", "function": {"name": "no_params"}},
        {"type": "function", "function": {}},  # skipped: no name
    ]
    out = vg._convert_openai_tools(tools)
    assert out == [
        {
            "functionDeclarations": [
                {
                    "name": "lookup",
                    "description": "d",
                    "parameters": {"type": "object"},
                },
                {"name": "no_params", "description": ""},
            ]
        }
    ]


def test_convert_openai_tools_empty_returns_none():
    assert vg._convert_openai_tools([]) is None
    assert vg._convert_openai_tools(None) is None
    assert vg._convert_openai_tools([{"function": {}}]) is None


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------


def test_messages_to_vertex_contents_system_and_turns():
    system, contents = vg._messages_to_vertex_contents(
        [
            {"role": "system", "content": "be strict"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
    )
    assert system == "be strict"
    assert [c["role"] for c in contents] == ["user", "model"]
    assert contents[0]["parts"] == [{"text": "hello"}]
    assert contents[1]["parts"] == [{"text": "hi"}]


def test_messages_to_vertex_contents_tool_roundtrip():
    vg._thought_signatures.clear()
    vg._thought_signatures["call-1"] = "sig-abc"
    system, contents = vg._messages_to_vertex_contents(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "lookup", "arguments": '{"k": 1}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "result",
            },
        ]
    )
    assert [c["role"] for c in contents] == ["model", "function"]
    fc_part = contents[0]["parts"][0]
    assert fc_part["functionCall"]["name"] == "lookup"
    assert fc_part["functionCall"]["args"] == {"k": 1}
    # thoughtSignature rides alongside functionCall at the part level
    assert fc_part["thoughtSignature"] == "sig-abc"
    fr = contents[1]["parts"][0]["functionResponse"]
    assert fr["name"] == "lookup"
    assert fr["response"] == {"result": "result"}
    vg._thought_signatures.clear()


def test_messages_to_vertex_contents_bad_tool_args_and_unknown_name():
    system, contents = vg._messages_to_vertex_contents(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c2",
                        "function": {"name": "f", "arguments": "{bad json"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c2", "content": "x"},
        ]
    )
    assert contents[0]["parts"][0]["functionCall"]["args"] == {}
    assert contents[1]["parts"][0]["functionResponse"]["name"] == "f"


def test_messages_to_vertex_contents_list_content_and_empty():
    system, contents = vg._messages_to_vertex_contents(
        [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]
    )
    assert contents[0]["parts"][0]["text"] == "ab"
    system, contents = vg._messages_to_vertex_contents([])
    assert contents == [{"role": "user", "parts": [{"text": ""}]}]
    assert system is None


def test_messages_to_vertex_contents_tool_role_without_pending_call():
    _, contents = vg._messages_to_vertex_contents(
        [{"role": "tool", "tool_call_id": "ghost", "content": "x"}]
    )
    assert contents[0]["parts"][0]["functionResponse"]["name"] == "unknown_tool"


# ---------------------------------------------------------------------------
# Response extraction helpers
# ---------------------------------------------------------------------------


def test_extract_function_calls_records_thought_signatures():
    vg._thought_signatures.clear()
    candidate = {
        "content": {
            "parts": [
                {
                    "functionCall": {"id": "fc1", "name": "a", "args": {"x": 1}},
                    "thoughtSignature": "sig-1",
                },
                "not-a-dict",
                {"text": "plain"},
            ]
        }
    }
    calls = vg._extract_function_calls(candidate)
    assert calls[0]["id"] == "fc1"
    assert calls[0]["type"] == "function"
    assert json.loads(calls[0]["function"]["arguments"]) == {"x": 1}
    assert vg._thought_signatures["fc1"] == "sig-1"
    vg._thought_signatures.clear()


def test_extract_function_calls_none_and_default_id():
    assert vg._extract_function_calls({}) is None
    candidate = {"content": {"parts": [{"functionCall": {"name": "b"}}]}}
    calls = vg._extract_function_calls(candidate)
    assert calls[0]["id"] == "call_0"


def test_extract_text_skips_thought_parts():
    candidate = {
        "content": {
            "parts": [
                {"text": "thinking", "thought": True},
                {"text": "answer"},
                {"thought": True},
            ]
        }
    }
    assert vg._extract_text(candidate) == "answer"
    # dict content without parts falls back to "" (no text leak)
    assert vg._extract_text({"content": {}}) == ""
    assert vg._extract_text({}) == ""


def test_to_openai_like_response_with_and_without_tools():
    usage = {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15}
    resp = vg._to_openai_like_response("text", usage, "model-x")
    assert resp.choices[0].message.content == "text"
    assert resp.choices[0].finish_reason == "stop"
    assert resp.choices[0].message.tool_calls is None
    assert resp.usage.prompt_tokens == 10
    assert resp.usage.completion_tokens == 5
    assert resp.usage.total_tokens == 15
    assert resp.model == "model-x"
    assert resp._vertex is True

    resp2 = vg._to_openai_like_response(
        "", {}, "m", tool_calls=[{"id": "1"}]
    )
    assert resp2.choices[0].finish_reason == "tool_calls"
    assert resp2.choices[0].message.tool_calls == [{"id": "1"}]


def test_to_openai_like_response_falls_back_to_litellm_keys():
    usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    resp = vg._to_openai_like_response("t", usage, "m")
    assert resp.usage.prompt_tokens == 3
    assert resp.usage.completion_tokens == 4
    assert resp.usage.total_tokens == 7


# ---------------------------------------------------------------------------
# JSON schema shaper
# ---------------------------------------------------------------------------


def test_vertex_json_schema_handles_refs_and_unions():
    schema = {
        "$defs": {
            "Point": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            }
        },
        "type": "object",
        "title": "Root",
        "properties": {
            "point": {"$ref": "#/$defs/Point"},
            "opt": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "d"},
            "nums": {"type": "array", "items": {"type": "number"}},
        },
    }
    out = vg._vertex_json_schema(schema)
    assert out["properties"]["point"]["type"] == "OBJECT"
    assert out["properties"]["point"]["properties"]["x"]["type"] == "INTEGER"
    assert out["properties"]["opt"]["type"] == "STRING"
    assert out["properties"]["opt"]["description"] == "d"
    assert out["properties"]["nums"]["items"]["type"] == "NUMBER"


def test_vertex_json_schema_type_list_and_fixes():
    out = vg._vertex_json_schema({"type": ["string", "null"]})
    assert out["type"] == "STRING"
    # leftover array without items gets a string fallback
    out2 = vg._vertex_json_schema({"type": "array", "minItems": 1})
    assert out2["items"] == {"type": "STRING"}
    # properties imply OBJECT
    out3 = vg._vertex_json_schema({"properties": {"a": {"type": "string"}}})
    assert out3["type"] == "OBJECT"
    assert vg._vertex_json_schema(None) is None


def test_vertex_json_schema_resolves_prefix_items():
    out = vg._vertex_json_schema(
        {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]}
    )
    assert out["type"] == "ARRAY"
    assert out["items"]["type"] == "STRING"


def test_vertex_json_schema_unresolvable_ref_becomes_object():
    out = vg._vertex_json_schema({"$ref": "#/$defs/Missing"})
    assert out == {"type": "OBJECT"}


# ---------------------------------------------------------------------------
# acomplete (HTTP mocked)
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


async def _run_acomplete(
    responses,
    *,
    token_returns=("tok",),
    raises=None,
    **kwargs,
):
    async def fake_post(url, headers, content):
        nonlocal responses
        resp = responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    client = mock.MagicMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post.side_effect = fake_post

    def fake_client(timeout):
        return client

    provider = mock.Mock()
    provider.get.side_effect = token_returns
    provider.refresh.return_value = "refreshed"

    call_kwargs = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "vertex_google/gemini-test",
    }
    call_kwargs.update(kwargs)
    with mock.patch.object(vg, "_httpx_client", fake_client), mock.patch.object(
        vg, "_token_provider", provider
    ), mock.patch.object(vg.asyncio, "sleep", new=mock.AsyncMock()):
        if raises is not None:
            with pytest.raises(raises):
                await vg.acomplete(**call_kwargs)
            return None
        return await vg.acomplete(**call_kwargs)


def _success_response():
    return FakeResponse(
        200,
        json_data={
            "candidates": [
                {"content": {"parts": [{"text": "ok"}]}}
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        },
    )


@pytest.mark.asyncio
async def test_acomplete_success_and_model_stripping():
    resp = await _run_acomplete(
        [_success_response()],
        token_returns=("tok-a",),
    )
    assert resp.choices[0].message.content == "ok"
    assert resp.model == "gemini-test"
    assert resp.usage.prompt_tokens == 5


@pytest.mark.asyncio
async def test_acomplete_401_refreshes_token_then_succeeds():
    resp = await _run_acomplete(
        [
            FakeResponse(401, text="unauthorized"),
            _success_response(),
        ],
        token_returns=("tok-a", "tok-b"),
    )
    assert resp.choices[0].message.content == "ok"
    assert resp.usage.completion_tokens == 3


@pytest.mark.asyncio
async def test_acomplete_retries_retryable_status_then_succeeds():
    resp = await _run_acomplete(
        [
            FakeResponse(429, text="slow down"),
            FakeResponse(500, text="boom"),
            _success_response(),
        ],
        token_returns=("tok-a",) * 3,
    )
    assert resp.choices[0].message.content == "ok"


@pytest.mark.asyncio
async def test_acomplete_raises_api_error_on_non_retryable_status():
    await _run_acomplete(
        [FakeResponse(400, text="bad request")],
        raises=vg.VertexAPIError,
    )


@pytest.mark.asyncio
async def test_acomplete_raises_when_no_candidates():
    await _run_acomplete(
        [FakeResponse(200, json_data={"usageMetadata": {}})],
        raises=vg.VertexAPIError,
    )


@pytest.mark.asyncio
async def test_acomplete_raises_when_project_missing(monkeypatch):
    monkeypatch.setattr(vg.settings, "gcp_project", "")
    with pytest.raises(vg.VertexAuthError, match="NOVELMIND_GCP_PROJECT"):
        await vg.acomplete([{"role": "user", "content": "hi"}], model="x")


@pytest.mark.asyncio
async def test_acomplete_raises_when_model_missing(monkeypatch):
    monkeypatch.setattr(vg.settings, "vertex_model", "")
    with pytest.raises(vg.VertexAPIError, match="未指定 Vertex 模型"):
        await vg.acomplete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_acomplete_max_retries_wrapped_in_api_error():
    err = await _run_acomplete(
        [RuntimeError("network down")] * 3,
        token_returns=("tok-a",) * 3,
        raises=vg.VertexAPIError,
    )


@pytest.mark.asyncio
async def test_acomplete_response_json_schema_and_tools():
    async def fake_post(url, headers, content):
        body = json.loads(content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["tools"] == [
            {"functionDeclarations": [{"name": "lookup", "description": ""}]}
        ]
        return FakeResponse(
            200,
            json_data={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "id": "fc-x",
                                        "name": "lookup",
                                        "args": {},
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {},
            },
        )

    client = mock.MagicMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.post.side_effect = fake_post

    provider = mock.Mock()
    provider.get.return_value = "tok"

    with mock.patch.object(vg, "_httpx_client", lambda timeout: client), mock.patch.object(
        vg, "_token_provider", provider
    ), mock.patch.object(vg.asyncio, "sleep", new=mock.AsyncMock()):
        resp = await vg.acomplete(
            [{"role": "user", "content": "hi"}],
            model="gemini-test",
            response_json_schema={
                "type": "object",
                "properties": {"a": {"type": "string"}},
            },
            tools=[
                {"type": "function", "function": {"name": "lookup"}}
            ],
        )
    assert resp.choices[0].message.tool_calls[0]["id"] == "fc-x"


@pytest.mark.asyncio
async def test_acomplete_retryable_status_raises_after_attempts():
    err = await _run_acomplete(
        [FakeResponse(503, text="unavailable")] * 3,
        token_returns=("tok-a",) * 3,
        raises=vg.VertexAPIError,
    )
