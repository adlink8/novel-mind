"""Unit tests for app.api.auth register/login/logout/me endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit

from app.config import settings

VALID_PASSWORD = "testpass123"


@pytest.fixture(autouse=True)
def _enable_interactive_auth_for_auth_endpoint_tests(monkeypatch):
    """Legacy auth endpoint cases opt in; the product default is disabled."""
    monkeypatch.setattr(settings, "auth_enabled", True)


def _payload(username: str, password: str = VALID_PASSWORD, **extra) -> dict:
    data = {
        "username": username,
        "email": f"{username}@example.com",
        "password": password,
    }
    data.update(extra)
    return data


async def test_register_success(client: AsyncClient):
    resp = await client.post("/api/auth/register", json=_payload("freshuser"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "freshuser"
    assert body["email"] == "freshuser@example.com"
    assert body["is_active"] is True


async def test_register_duplicate_username(client: AsyncClient):
    await client.post("/api/auth/register", json=_payload("dupuser"))
    resp = await client.post("/api/auth/register", json=_payload("dupuser"))
    assert resp.status_code == 400
    assert "用户名" in resp.json()["detail"]


async def test_register_duplicate_email(client: AsyncClient):
    await client.post("/api/auth/register", json=_payload("mailuser1"))
    resp = await client.post(
        "/api/auth/register",
        json={
            "username": "mailuser2",
            "email": "mailuser1@example.com",
            "password": VALID_PASSWORD,
        },
    )
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"]


async def test_register_normalizes_case(client: AsyncClient):
    resp = await client.post(
        "/api/auth/register",
        json=_payload("MiXeDcAsE", email="MiXeD@Example.com"),
    )
    assert resp.status_code == 201
    assert resp.json()["username"] == "mixedcase"


async def test_register_validation_error_on_bad_email(client: AsyncClient):
    resp = await client.post(
        "/api/auth/register",
        json=_payload("badmailuser", email="not-an-email"),
    )
    assert resp.status_code == 422


async def test_register_second_user_not_superuser(client: AsyncClient):
    # First user becomes bootstrap admin; second must NOT be superuser.
    await client.post("/api/auth/register", json=_payload("firstone"))
    resp = await client.post("/api/auth/register", json=_payload("secondone"))
    assert resp.status_code == 201
    assert resp.json()["username"] == "secondone"


async def test_login_success_sets_cookie(client: AsyncClient):
    await client.post("/api/auth/register", json=_payload("loginuser"))
    resp = await client.post(
        "/api/auth/login",
        json={"username": "loginuser", "password": VALID_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user_id"] > 0
    assert body["username"] == "loginuser"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "novelmind_session=" in set_cookie
    assert "HttpOnly" in set_cookie


async def test_login_wrong_password_401(client: AsyncClient):
    await client.post("/api/auth/register", json=_payload("wrongpass"))
    resp = await client.post(
        "/api/auth/login",
        json={"username": "wrongpass", "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_login_unknown_user_401(client: AsyncClient):
    resp = await client.post(
        "/api/auth/login",
        json={"username": "ghost", "password": VALID_PASSWORD},
    )
    assert resp.status_code == 401


async def test_login_disabled_user_401(client: AsyncClient, db_session):
    await client.post("/api/auth/register", json=_payload("disableduser"))
    from sqlalchemy import select

    from app.models import User

    user = await db_session.scalar(select(User).where(User.username == "disableduser"))
    user.is_active = False
    await db_session.commit()
    resp = await client.post(
        "/api/auth/login",
        json={"username": "disableduser", "password": VALID_PASSWORD},
    )
    assert resp.status_code == 401


async def test_logout_clears_cookie(client: AsyncClient):
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204
    assert "novelmind_session" in resp.headers.get("set-cookie", "")


async def test_get_me_authenticated(auth_client: AsyncClient):
    resp = await auth_client.get("/api/auth/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "testuser"
    assert body["is_active"] is True


async def test_get_me_anonymous_401(client: AsyncClient):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_register_and_login_are_closed_when_auth_disabled(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "auth_enabled", False)

    register_resp = await client.post(
        "/api/auth/register", json=_payload("closeduser")
    )
    login_resp = await client.post(
        "/api/auth/login",
        json={"username": "closeduser", "password": VALID_PASSWORD},
    )

    assert register_resp.status_code == 403
    assert register_resp.json()["detail"] == "注册功能已关闭"
    assert login_resp.status_code == 403
    assert login_resp.json()["detail"] == "登录功能已关闭"


async def test_local_auto_login_disabled_by_default(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "local_auto_login_username", "")
    resp = await client.post(
        "/api/auth/local-auto-login",
        headers={"Origin": "http://127.0.0.1:3001"},
    )
    assert resp.status_code == 403


async def test_local_auto_login_issues_session_for_configured_user(
    client: AsyncClient, monkeypatch
):
    await client.post("/api/auth/register", json=_payload("localreader"))
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "local_auto_login_username", "localreader")

    resp = await client.post(
        "/api/auth/local-auto-login",
        headers={"Origin": "http://127.0.0.1:3001"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == "localreader"
    assert resp.json()["access_token"]
    assert "novelmind_session=" in resp.headers.get("set-cookie", "")


async def test_local_auto_login_rejects_unapproved_origin(
    client: AsyncClient, monkeypatch
):
    await client.post("/api/auth/register", json=_payload("originreader"))
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "local_auto_login_username", "originreader")

    resp = await client.post(
        "/api/auth/local-auto-login",
        headers={"Origin": "https://attacker.example"},
    )

    assert resp.status_code == 403


async def test_local_auto_login_rejects_production_mode(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "local_auto_login_username", "localreader")
    resp = await client.post(
        "/api/auth/local-auto-login",
        headers={"Origin": "http://127.0.0.1:3001"},
    )
    assert resp.status_code == 403


async def test_register_bootstrap_wrong_token_403(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_token", "bootstrap-token-1")
    monkeypatch.setattr(settings, "debug", True)
    resp = await client.post(
        "/api/auth/register", json=_payload("bootstrapbad", bootstrap_token="wrong")
    )
    assert resp.status_code == 403


async def test_register_production_without_token_fail_closed(
    client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(settings, "bootstrap_admin_token", "")
    monkeypatch.setattr(settings, "debug", False)
    resp = await client.post("/api/auth/register", json=_payload("prodblocked"))
    assert resp.status_code == 403


# ── direct-call coverage for route handlers ──
# (HTTP-via-httpx handler code is not traced by coverage on this Windows/Python
# build; direct calls keep the lines measurable and are equivalent behavior.)


async def test_register_direct_call(db_session):
    from app.api.auth import RegisterRequest, register

    request = RegisterRequest(
        username="directuser", email="directuser@example.com", password=VALID_PASSWORD
    )
    user = await register(request, db_session)
    assert user.username == "directuser"
    assert user.is_active is True  # first user is bootstrap admin

    from app.models import User
    from sqlalchemy import select

    row = await db_session.scalar(select(User).where(User.username == "directuser"))
    assert row.is_superuser is True


async def test_login_direct_call(db_session):
    from fastapi import Response

    from app.api.auth import LoginRequest, login
    from app.core.security import hash_password
    from app.models import User

    user = User(
        username="directlogin",
        email="directlogin@example.com",
        hashed_password=hash_password(VALID_PASSWORD),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    response = Response()
    token_resp = await login(
        LoginRequest(username="directlogin", password=VALID_PASSWORD),
        response,
        db_session,
    )
    assert token_resp.user_id == user.id
    assert token_resp.access_token
    assert "novelmind_session=" in response.headers["set-cookie"]


async def test_login_direct_call_wrong_password_401(db_session):
    from fastapi import HTTPException, Response

    from app.api.auth import LoginRequest, login
    from app.core.security import hash_password
    from app.models import User

    user = User(
        username="directwrong",
        email="directwrong@example.com",
        hashed_password=hash_password(VALID_PASSWORD),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await login(
            LoginRequest(username="directwrong", password="wrongpass"),
            Response(),
            db_session,
        )
    assert exc.value.status_code == 401


async def test_logout_direct_call():
    from fastapi import Response

    from app.api.auth import logout

    response = Response()
    await logout(response)
    assert "novelmind_session" in response.headers["set-cookie"]


async def test_get_me_direct_call(db_session):
    from app.api.auth import get_me
    from app.models import User

    user = User(
        username="directme",
        email="directme@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    body = await get_me(user)
    assert body.id == user.id
    assert body.username == "directme"


async def test_register_direct_duplicate_username(db_session):
    from fastapi import HTTPException

    from app.api.auth import RegisterRequest, register

    payload = RegisterRequest(
        username="dupdirect", email="dupdirect@example.com", password=VALID_PASSWORD
    )
    await register(payload, db_session)
    with pytest.raises(HTTPException) as exc:
        await register(
            RegisterRequest(
                username="dupdirect",
                email="other@example.com",
                password=VALID_PASSWORD,
            ),
            db_session,
        )
    assert exc.value.status_code == 400


async def test_register_direct_duplicate_email(db_session):
    from fastapi import HTTPException

    from app.api.auth import RegisterRequest, register

    payload = RegisterRequest(
        username="maildirect", email="maildirect@example.com", password=VALID_PASSWORD
    )
    await register(payload, db_session)
    with pytest.raises(HTTPException) as exc:
        await register(
            RegisterRequest(
                username="othername",
                email="maildirect@example.com",
                password=VALID_PASSWORD,
            ),
            db_session,
        )
    assert exc.value.status_code == 400


async def test_register_direct_bootstrap_token_branches(db_session, monkeypatch):
    from fastapi import HTTPException

    from app.api.auth import RegisterRequest, register

    monkeypatch.setattr(settings, "bootstrap_admin_token", "bootstrap-token-xyz")
    monkeypatch.setattr(settings, "debug", True)

    with pytest.raises(HTTPException) as exc:
        await register(
            RegisterRequest(
                username="bootstrapbad",
                email="bootstrapbad@example.com",
                password=VALID_PASSWORD,
                bootstrap_token="wrong",
            ),
            db_session,
        )
    assert exc.value.status_code == 403

    user = await register(
        RegisterRequest(
            username="bootstrapgood",
            email="bootstrapgood@example.com",
            password=VALID_PASSWORD,
            bootstrap_token="bootstrap-token-xyz",
        ),
        db_session,
    )
    assert user.username == "bootstrapgood"


async def test_register_direct_production_fail_closed(db_session, monkeypatch):
    from fastapi import HTTPException

    from app.api.auth import RegisterRequest, register

    monkeypatch.setattr(settings, "bootstrap_admin_token", "")
    monkeypatch.setattr(settings, "debug", False)
    with pytest.raises(HTTPException) as exc:
        await register(
            RegisterRequest(
                username="proddirect",
                email="proddirect@example.com",
                password=VALID_PASSWORD,
            ),
            db_session,
        )
    assert exc.value.status_code == 403


async def test_register_direct_transfers_legacy_owner_assets(db_session):
    from app.api.auth import RegisterRequest, register
    from app.models import AIModelConfig, Novel, User

    legacy = User(
        username="legacy-owner",
        email="legacy@example.com",
        hashed_password="x",
        is_active=False,  # 离线预置：不计入活跃用户，首个新注册用户成为 bootstrap admin
    )
    db_session.add(legacy)
    await db_session.flush()
    novel = Novel(title="legacy novel", owner_id=legacy.id, status="ready")
    db_session.add(novel)
    model = AIModelConfig(
        owner_id=legacy.id,
        name="legacy model",
        provider="openai",
        model_id="gpt-4o",
    )
    db_session.add(model)
    await db_session.flush()
    legacy_id = legacy.id

    user = await register(
        RegisterRequest(
            username="assetinheritor",
            email="assetinheritor@example.com",
            password=VALID_PASSWORD,
        ),
        db_session,
    )
    await db_session.flush()
    assert user.id != legacy_id
    novel = await db_session.get(Novel, novel.id)
    model = await db_session.get(AIModelConfig, model.id)
    assert novel.owner_id == user.id
    assert model.owner_id == user.id


async def test_login_direct_disabled_user_401(db_session):
    from fastapi import HTTPException, Response

    from app.api.auth import LoginRequest, login
    from app.core.security import hash_password
    from app.models import User

    user = User(
        username="directdisabled",
        email="directdisabled@example.com",
        hashed_password=hash_password(VALID_PASSWORD),
        is_active=False,
    )
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await login(
            LoginRequest(username="directdisabled", password=VALID_PASSWORD),
            Response(),
            db_session,
        )
    assert exc.value.status_code == 401


async def test_register_direct_second_user_skips_bootstrap(db_session):
    """第二个注册用户 is_bootstrap_admin=False → 跳过 bootstrap 与 legacy 迁移。"""
    from app.api.auth import RegisterRequest, register
    from app.models import User
    from sqlalchemy import select

    await register(
        RegisterRequest(
            username="firstadmin",
            email="firstadmin@example.com",
            password=VALID_PASSWORD,
        ),
        db_session,
    )
    user = await register(
        RegisterRequest(
            username="seconduser",
            email="seconduser@example.com",
            password=VALID_PASSWORD,
        ),
        db_session,
    )
    row = await db_session.scalar(select(User).where(User.username == "seconduser"))
    assert row.is_superuser is False
    assert user.username == "seconduser"
