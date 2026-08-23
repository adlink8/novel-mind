"""
AI 模型配置 API 测试

覆盖范围:
- 空列表查询
- 创建模型配置
- 名称唯一性校验
- 更新模型配置
- 设为默认模型
- 删除（软删除）
- 删除后列表不再显示
- 404 边界

注意:
- test_connection 需要外部 API Key，此处仅测试模型不存在的情况
"""

import pytest
import httpx
from unittest.mock import patch

pytestmark = pytest.mark.unit
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_models_list_empty(auth_client: AsyncClient):
    """空数据库下模型列表为空数组"""
    response = await auth_client.get("/api/models")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_list_supported_provider_profiles(auth_client: AsyncClient):
    """设置页只返回当前支持的五种供应商协议，不再暴露 Vertex。"""
    response = await auth_client.get("/api/models/providers")
    assert response.status_code == 200
    providers = {item["id"]: item for item in response.json()}
    assert set(providers) == {
        "openai",
        "anthropic",
        "gemini",
        "ollama",
        "custom",
    }
    assert providers["openai"]["default_base_url"] == "https://api.openai.com/v1"
    assert providers["anthropic"]["credential_kind"] == "api_key"
    assert providers["gemini"]["default_base_url"].endswith("/v1beta")
    assert providers["ollama"]["credential_required"] is False
    assert providers["custom"]["default_base_url"] is None


@pytest.mark.asyncio
async def test_vertex_provider_is_rejected_for_new_model_configs(
    auth_client: AsyncClient,
):
    response = await auth_client.post(
        "/api/models",
        json={
            "name": "legacy-vertex",
            "provider": "vertex_google",
            "model_id": "gemini-provider-a",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_model(auth_client: AsyncClient):
    """创建 AI 模型配置"""
    payload = {
        "name": "测试模型",
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "api_key": "sk-test-key",
        "tier": "balanced",
        "max_tokens": 2048,
        "temperature": 0.5,
        "is_default": False,
    }
    response = await auth_client.post("/api/models", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "测试模型"
    assert data["provider"] == "openai"
    assert data["model_id"] == "gpt-4o-mini"
    assert data["tier"] == "balanced"
    assert data["max_tokens"] == 2048
    assert data["temperature"] == 0.5
    assert data["is_default"] is False
    assert data["is_active"] is True
    # api_key 不应暴露
    assert "api_key" not in data


@pytest.mark.asyncio
async def test_discover_openai_compatible_models_from_configured_url(
    auth_client: AsyncClient,
    monkeypatch,
):
    """设置页可以通过真实 Base URL 获取模型列表，且响应不回显密钥。"""
    from app.config import settings

    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "object": "list",
                "data": [
                    {"id": "provider-model-a"},
                    {"id": "provider-model-b"},
                ],
            }

    class FakeAsyncClient:
        last_url = None
        last_headers = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers):
            type(self).last_url = url
            type(self).last_headers = headers
            return FakeResponse()

    with patch("httpx.AsyncClient", FakeAsyncClient):
        response = await auth_client.post(
            "/api/models/discover",
            json={
                "provider": "custom",
                "base_url": "http://127.0.0.1:9999/v1",
                "api_key": "provider-secret",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "models": [
            {"id": "provider-model-a", "name": "provider-model-a"},
            {"id": "provider-model-b", "name": "provider-model-b"},
        ]
    }
    assert FakeAsyncClient.last_url == "http://127.0.0.1:9999/v1/models"
    assert FakeAsyncClient.last_headers == {
        "Authorization": "Bearer provider-secret",
        "Accept": "application/json",
    }
    assert "provider-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "payload", "expected_path", "expected_headers", "expected_models"),
    [
        (
            "openai",
            {"data": [{"id": "gpt-provider-a", "owned_by": "openai"}]},
            "/v1/models",
            {"Authorization": "Bearer provider-secret"},
            [{"id": "gpt-provider-a", "name": "gpt-provider-a"}],
        ),
        (
            "custom",
            {"data": [{"id": "compatible-model-a"}]},
            "/v1/models",
            {"Authorization": "Bearer provider-secret"},
            [{"id": "compatible-model-a", "name": "compatible-model-a"}],
        ),
        (
            "anthropic",
            {"data": [{"id": "claude-provider-a", "display_name": "Claude A"}]},
            "/v1/models",
            {
                "x-api-key": "provider-secret",
                "anthropic-version": "2023-06-01",
            },
            [{"id": "claude-provider-a", "name": "Claude A"}],
        ),
        (
            "gemini",
            {
                "models": [
                    {
                        "name": "models/gemini-provider-a",
                        "displayName": "Gemini A",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/embedding-provider-a",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
            "/v1beta/models",
            {"x-goog-api-key": "provider-secret"},
            [{"id": "gemini-provider-a", "name": "Gemini A"}],
        ),
        (
            "ollama",
            {"models": [{"name": "qwen3:8b", "model": "qwen3:8b"}]},
            "/api/tags",
            {},
            [{"id": "qwen3:8b", "name": "qwen3:8b"}],
        ),
    ],
)
async def test_discover_adapts_all_supported_provider_protocols(
    auth_client: AsyncClient,
    monkeypatch,
    provider,
    payload,
    expected_path,
    expected_headers,
    expected_models,
):
    """五种设置页供应商都使用各自的目录路径、认证头和响应格式。"""
    from app.config import settings

    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeAsyncClient:
        last_url = None
        last_headers = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers):
            type(self).last_url = url
            type(self).last_headers = headers
            return FakeResponse()

    base_url = {
        "gemini": "http://127.0.0.1:9999/v1beta",
        "ollama": "http://127.0.0.1:9999",
    }.get(provider, "http://127.0.0.1:9999/v1")
    api_key = None if provider == "ollama" else "provider-secret"

    with patch("httpx.AsyncClient", FakeAsyncClient):
        response = await auth_client.post(
            "/api/models/discover",
            json={"provider": provider, "base_url": base_url, "api_key": api_key},
        )

    assert response.status_code == 200
    assert response.json()["models"] == expected_models
    assert FakeAsyncClient.last_url.endswith(expected_path)
    assert FakeAsyncClient.last_headers["Accept"] == "application/json"
    for key, value in expected_headers.items():
        assert FakeAsyncClient.last_headers[key] == value
    if provider == "ollama":
        assert "Authorization" not in FakeAsyncClient.last_headers


@pytest.mark.asyncio
async def test_discover_rejects_unknown_provider(auth_client: AsyncClient):
    response = await auth_client.post(
        "/api/models/discover",
        json={
            "provider": "unknown-provider",
            "base_url": "https://api.openai.com/v1",
            "api_key": "provider-secret",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_discover_rejects_unsafe_url_before_outbound_request(
    auth_client: AsyncClient,
):
    """模型发现不能访问 metadata、回环或其他未列入白名单的地址。"""

    class UnexpectedAsyncClient:
        def __init__(self, **kwargs):
            raise AssertionError("不安全 URL 不应建立外部客户端")

    with patch("httpx.AsyncClient", UnexpectedAsyncClient):
        response = await auth_client.post(
            "/api/models/discover",
            json={
                "provider": "custom",
                "base_url": "http://169.254.169.254/latest/meta-data",
                "api_key": "provider-secret",
            },
        )

    assert response.status_code == 400
    assert "provider-secret" not in response.text


@pytest.mark.asyncio
async def test_discover_provider_failure_is_safe(
    auth_client: AsyncClient,
    monkeypatch,
):
    """上游不可用时返回稳定错误，且不泄露连接密钥。"""
    from app.config import settings

    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")

    class FailingAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers):
            raise httpx.ConnectError("provider-secret unavailable")

    with patch("httpx.AsyncClient", FailingAsyncClient):
        response = await auth_client.post(
            "/api/models/discover",
            json={
                "provider": "custom",
                "base_url": "http://127.0.0.1:9999/v1",
                "api_key": "provider-secret",
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "无法从该地址获取模型列表"
    assert "provider-secret" not in response.text


@pytest.mark.asyncio
async def test_create_model_duplicate_name(auth_client: AsyncClient):
    """同名模型创建应返回 400"""
    payload = {
        "name": "唯一模型",
        "provider": "openai",
        "model_id": "gpt-4o",
    }
    resp1 = await auth_client.post("/api/models", json=payload)
    assert resp1.status_code == 200

    resp2 = await auth_client.post("/api/models", json=payload)
    assert resp2.status_code == 400
    assert "已存在" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_set_default_model(auth_client: AsyncClient):
    """设置默认模型，其他模型应取消默认状态"""
    # 创建两个模型
    model_a = await auth_client.post(
        "/api/models",
        json={
            "name": "模型A",
            "provider": "openai",
            "model_id": "gpt-4o",
            "is_default": True,
        },
    )
    assert model_a.status_code == 200
    id_a = model_a.json()["id"]
    assert model_a.json()["is_default"] is True

    model_b = await auth_client.post(
        "/api/models",
        json={
            "name": "模型B",
            "provider": "anthropic",
            "model_id": "claude-3-haiku",
            "is_default": False,
        },
    )
    id_b = model_b.json()["id"]

    # 将 B 设为默认
    resp = await auth_client.post(f"/api/models/{id_b}/default")
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True
    assert resp.json()["name"] == "模型B"

    # A 应被取消默认
    list_resp = await auth_client.get("/api/models")
    models = list_resp.json()
    model_a_after = next((m for m in models if m["id"] == id_a), None)
    model_b_after = next((m for m in models if m["id"] == id_b), None)
    assert model_a_after["is_default"] is False
    assert model_b_after["is_default"] is True


@pytest.mark.asyncio
async def test_update_model(auth_client: AsyncClient):
    """更新模型配置（部分字段）"""
    create_resp = await auth_client.post(
        "/api/models",
        json={
            "name": "更新测试",
            "provider": "openai",
            "model_id": "gpt-4o",
            "temperature": 0.7,
        },
    )
    model_id = create_resp.json()["id"]

    update_resp = await auth_client.put(
        f"/api/models/{model_id}",
        json={
            "temperature": 0.3,
            "max_tokens": 8192,
        },
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["temperature"] == 0.3
    assert data["max_tokens"] == 8192
    assert data["name"] == "更新测试"  # 未更新字段保持不变


@pytest.mark.asyncio
async def test_delete_model_soft_delete(auth_client: AsyncClient):
    """删除模型为软删除，列表中不再显示"""
    create_resp = await auth_client.post(
        "/api/models",
        json={
            "name": "待删除",
            "provider": "ollama",
            "model_id": "qwen2",
        },
    )
    model_id = create_resp.json()["id"]

    # 删除
    del_resp = await auth_client.delete(f"/api/models/{model_id}")
    assert del_resp.status_code == 200
    assert "已删除" in del_resp.json()["message"]

    # 列表中不应再出现
    list_resp = await auth_client.get("/api/models")
    assert model_id not in [m["id"] for m in list_resp.json()]


@pytest.mark.asyncio
async def test_update_model_not_found(auth_client: AsyncClient):
    """更新不存在的模型返回 404"""
    resp = await auth_client.put("/api/models/9999", json={"temperature": 0.5})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_set_default_model_not_found(auth_client: AsyncClient):
    """设置不存在的模型为默认返回 404"""
    resp = await auth_client.post("/api/models/9999/default")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_model_not_found(auth_client: AsyncClient):
    """删除不存在的模型返回 404"""
    resp = await auth_client.delete("/api/models/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_test_model_not_found(auth_client: AsyncClient):
    """测试不存在的模型连通性返回 404"""
    resp = await auth_client.post("/api/models/9999/test")
    assert resp.status_code == 404
