"""
Bootstrap 管理员门禁对抗测试（2026-08-07 全仓审计 NM-?：首个注册用户自动 superuser）。

风险：公开注册端点在首个用户注册时无条件授予 superuser；空库部署被外部注册者
抢占管理权。本测试验证 fail-closed 门禁：

- 配置了 NOVELMIND_BOOTSTRAP_ADMIN_TOKEN 时：首个注册必须携带匹配的
  bootstrap_token，缺失/不匹配 → 403；
- 生产模式（debug=False）未配置 token 时：注册接口 fail-closed → 403；
- bootstrap 一次性：首个用户建立后，后续注册即使携带正确 token 也不授予 superuser；
- 本地开发（debug=True）未配置 token：保持原行为（首注册成为 admin），
  兼容既有开发流程与测试。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models import User

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

TEST_TOKEN = "bootstrap-super-secret-123"
PASSWORD = "correct-horse-123"


def _payload(username: str, token: str | None = None) -> dict:
    data = {
        "username": username,
        "email": f"{username}@example.com",
        "password": PASSWORD,
    }
    if token is not None:
        data["bootstrap_token"] = token
    return data


async def _user_superuser(client: AsyncClient, db_session, username: str) -> bool:
    user = await db_session.scalar(
        select(User).where(User.username == username.strip().lower())
    )
    assert user is not None, f"user {username} not persisted"
    return bool(user.is_superuser)


async def _user_exists(db_session, username: str) -> bool:
    user = await db_session.scalar(
        select(User).where(User.username == username.strip().lower())
    )
    return user is not None


async def test_bootstrap_token_required_when_configured(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "bootstrap_admin_token", TEST_TOKEN)
    monkeypatch.setattr(settings, "debug", True)

    # 缺失 token → 403
    resp = await client.post("/api/auth/register", json=_payload("hacker-no-token"))
    assert resp.status_code == 403

    # 错误 token → 403
    resp = await client.post(
        "/api/auth/register", json=_payload("hacker-bad-token", "wrong-token")
    )
    assert resp.status_code == 403

    # 正确 token → 201 且成为 superuser
    resp = await client.post(
        "/api/auth/register", json=_payload("legit-admin", TEST_TOKEN)
    )
    assert resp.status_code == 201
    assert await _user_superuser(client, db_session, "legit-admin") is True


async def test_bootstrap_is_one_time_and_closes(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "bootstrap_admin_token", TEST_TOKEN)
    monkeypatch.setattr(settings, "debug", True)

    # 首个用户携带 token → superuser
    resp = await client.post(
        "/api/auth/register", json=_payload("first-admin", TEST_TOKEN)
    )
    assert resp.status_code == 201

    # 后续用户即使携带正确 token 也不再授予 superuser
    resp = await client.post(
        "/api/auth/register", json=_payload("second-user", TEST_TOKEN)
    )
    assert resp.status_code == 201
    assert await _user_superuser(client, db_session, "second-user") is False


async def test_production_without_token_fails_closed(
    client: AsyncClient, db_session, monkeypatch
):
    # 生产模式 + 未配置 bootstrap token：注册 fail-closed
    monkeypatch.setattr(settings, "bootstrap_admin_token", "")
    monkeypatch.setattr(settings, "debug", False)

    resp = await client.post(
        "/api/auth/register", json=_payload("unauthorized-registrant")
    )
    assert resp.status_code == 403
    # fail-closed：注册被拒绝，且用户未落库
    assert await _user_exists(db_session, "unauthorized-registrant") is False


async def test_production_with_token_allows_bootstrap(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "bootstrap_admin_token", TEST_TOKEN)
    monkeypatch.setattr(settings, "debug", False)

    # 缺失 token → 403
    resp = await client.post("/api/auth/register", json=_payload("prod-hacker"))
    assert resp.status_code == 403

    # 携带 token → superuser
    resp = await client.post(
        "/api/auth/register", json=_payload("prod-admin", TEST_TOKEN)
    )
    assert resp.status_code == 201
    assert await _user_superuser(client, db_session, "prod-admin") is True


async def test_dev_without_token_keeps_legacy_behavior(
    client: AsyncClient, db_session, monkeypatch
):
    # 本地开发（debug=True）且未配置 token：保持原行为，首注册成为 superuser
    monkeypatch.setattr(settings, "bootstrap_admin_token", "")
    monkeypatch.setattr(settings, "debug", True)

    resp = await client.post("/api/auth/register", json=_payload("local-dev-user"))
    assert resp.status_code == 201
    assert await _user_superuser(client, db_session, "local-dev-user") is True
