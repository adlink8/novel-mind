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
