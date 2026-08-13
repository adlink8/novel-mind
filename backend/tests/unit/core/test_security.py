"""Unit tests for app.core.security authentication helpers.

Covers password hashing edge cases, JWT create/decode, request-origin
validation, cookie-auth guard, require_user / require_gateway_token /
require_agent_actor fail-closed paths, and get_current_user DB outcomes.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

pytestmark = pytest.mark.unit

from app.config import settings
from app.core.security import (
    AUTH_COOKIE_NAME,
    AgentActor,
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    require_agent_actor,
    require_gateway_token,
    require_user,
    validate_cookie_request_origin,
    validate_password_length,
    verify_password,
)
from app.models import User


@pytest.fixture(autouse=True)
def _enable_interactive_auth_for_security_unit_tests(monkeypatch):
    """Keep legacy token-path assertions explicit while auth defaults off."""
    monkeypatch.setattr(settings, "auth_enabled", True)


# ── password helpers ──


def test_verify_password_rejects_non_ascii_hash():
    # A stored hash containing non-ASCII bytes fails closed (UnicodeEncodeError).
    assert verify_password("password123", "密碼哈希$2b$12$abcdef") is False


def test_verify_password_rejects_malformed_hash():
    # bcrypt raises ValueError on structurally invalid hashes → fail closed.
    assert verify_password("password123", "$2b$12$too-short") is False


def test_hash_password_and_verify_roundtrip():
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$2b$")
    assert verify_password("correct horse battery", hashed)


def test_validate_password_length_rejects_over_72_bytes():
    with pytest.raises(ValueError):
        validate_password_length("密" * 25)  # 75 bytes UTF-8


def test_validate_password_length_ascii_72_ok():
    assert validate_password_length("a" * 72) == "a" * 72


def test_validate_password_length_multibyte_boundary_ok():
    # 24 CJK chars = 72 bytes → allowed.
    assert validate_password_length("密" * 24) == "密" * 24


# ── origin guard ──


def _request(method: str = "GET", headers: dict[str, str] | None = None) -> Request:
    hdrs = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    return Request({"type": "http", "method": method, "headers": hdrs})


def test_validate_cookie_request_origin_get_passes():
    assert validate_cookie_request_origin(_request("GET")) is None


def test_validate_cookie_request_origin_post_without_origin_403():
    with pytest.raises(HTTPException) as exc:
        validate_cookie_request_origin(_request("POST"))
    assert exc.value.status_code == 403


def test_validate_cookie_request_origin_post_referer_without_scheme_403():
    req = _request("POST", {"Referer": "//example.com/path"})
    with pytest.raises(HTTPException) as exc:
        validate_cookie_request_origin(req)
    assert exc.value.status_code == 403


def test_validate_cookie_request_origin_post_disallowed_origin_403(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["http://allowed.example"])
    req = _request("POST", {"Origin": "http://evil.example"})
    with pytest.raises(HTTPException) as exc:
        validate_cookie_request_origin(req)
    assert exc.value.status_code == 403


def test_validate_cookie_request_origin_post_allowed_origin_passes(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["http://allowed.example"])
    req = _request("POST", {"Origin": "http://allowed.example/"})
    assert validate_cookie_request_origin(req) is None


def test_validate_cookie_request_origin_post_allowed_referer_passes(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["http://referrer.example"])
    req = _request("POST", {"Referer": "http://referrer.example/deep/link"})
    assert validate_cookie_request_origin(req) is None


def test_validate_cookie_request_origin_put_disallowed_403(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", ["http://allowed.example"])
    with pytest.raises(HTTPException) as exc:
        validate_cookie_request_origin(_request("DELETE"))
    assert exc.value.status_code == 403


def test_validate_cookie_request_origin_referer_parse_error_403(monkeypatch):
    # urlsplit raises on a malformed IPv6 referer → _request_origin returns None → 403.
    monkeypatch.setattr(settings, "cors_origins", ["http://allowed.example"])
    req = _request("POST", {"Referer": "http://[::1"})
    with pytest.raises(HTTPException) as exc:
        validate_cookie_request_origin(req)
    assert exc.value.status_code == 403


# ── JWT helpers ──


def test_create_access_token_with_default_expiry():
    token = create_access_token({"sub": "42"})
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["iss"] == "novelmind"
    assert payload["aud"] == "novelmind-web"
    assert "jti" in payload


def test_create_access_token_with_custom_expires_delta():
    token = create_access_token({"sub": "7"}, expires_delta=timedelta(minutes=5))
    assert decode_access_token(token)["sub"] == "7"


def test_decode_access_token_returns_none_for_invalid_token():
    assert decode_access_token("not-a-real-jwt") is None
    assert decode_access_token("") is None


def test_decode_access_token_returns_none_for_wrong_audience(monkeypatch):
    token = create_access_token({"sub": "1"})
    # Mutate audience by encoding with a different audience.
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": "1", "aud": "other-audience"},
        settings.secret_key,
        algorithm="HS256",
    )
    assert decode_access_token(forged) is None
    assert decode_access_token(token) is not None


# ── get_current_user ──


async def _make_user(db, *, username="alice", is_active=True, is_superuser=False):
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="unused",
        is_active=is_active,
        is_superuser=is_superuser,
    )
    db.add(user)
    await db.flush()
    return user


async def test_get_current_user_no_token_returns_none(db_session):
    await _make_user(db_session)
    request = _request()
    assert await get_current_user(request, None, db_session) is None


async def test_get_current_user_uses_default_workspace_when_auth_disabled(
    db_session, monkeypatch
):
    user = await _make_user(db_session, username="admin")
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "local_auto_login_username", "admin")

    result = await get_current_user(_request(), None, db_session)

    assert result.id == user.id


async def test_get_current_user_ignores_stale_jwt_when_auth_disabled(
    db_session, monkeypatch
):
    default_user = await _make_user(db_session, username="admin")
    stale_user = await _make_user(db_session, username="old-user")
    stale_token = create_access_token({"sub": str(stale_user.id)})
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=stale_token
    )
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "local_auto_login_username", "admin")

    result = await get_current_user(_request(), credentials, db_session)

    assert result.id == default_user.id


async def test_get_current_user_fails_when_default_workspace_is_missing(
    db_session, monkeypatch
):
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "local_auto_login_username", "missing")

    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request(), None, db_session)

    assert exc.value.status_code == 503


async def test_get_current_user_cookie_no_token_returns_none(db_session):
    await _make_user(db_session)
    req = _request("GET", {"Cookie": "other=1"})
    assert await get_current_user(req, None, db_session) is None


async def test_get_current_user_invalid_jwt_401(db_session):
    request = _request()
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad.token")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, creds, db_session)
    assert exc.value.status_code == 401


async def test_get_current_user_cookie_post_without_origin_403(db_session):
    user = await _make_user(db_session)
    token = create_access_token({"sub": str(user.id)})
    req = _request("POST", {"Cookie": f"{AUTH_COOKIE_NAME}={token}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(req, None, db_session)
    assert exc.value.status_code == 403


async def test_get_current_user_cookie_get_valid_returns_user(db_session):
    user = await _make_user(db_session)
    token = create_access_token({"sub": str(user.id)})
    req = _request("GET", {"Cookie": f"{AUTH_COOKIE_NAME}={token}"})
    result = await get_current_user(req, None, db_session)
    assert result is not None and result.id == user.id


async def test_get_current_user_payload_missing_sub_401(db_session):
    token = create_access_token({"role": "admin"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request(), creds, db_session)
    assert exc.value.status_code == 401


async def test_get_current_user_unknown_user_401(db_session):
    token = create_access_token({"sub": "999999"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request(), creds, db_session)
    assert exc.value.status_code == 401


async def test_get_current_user_disabled_user_401(db_session):
    user = await _make_user(db_session, is_active=False)
    token = create_access_token({"sub": str(user.id)})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request(), creds, db_session)
    assert exc.value.status_code == 401


async def test_get_current_user_success(db_session):
    user = await _make_user(db_session)
    token = create_access_token({"sub": str(user.id)})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    result = await get_current_user(_request(), creds, db_session)
    assert result.id == user.id


# ── require_user ──


async def test_require_user_missing_user_401():
    with pytest.raises(HTTPException) as exc:
        await require_user(None)
    assert exc.value.status_code == 401


async def test_require_user_returns_user():
    user = SimpleNamespace(id=1)
    assert await require_user(user) is user


# ── require_gateway_token ──


async def test_require_gateway_token_not_configured_401(monkeypatch):
    monkeypatch.setattr(settings, "novelmind_gateway_token", "")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="x")
    with pytest.raises(HTTPException) as exc:
        await require_gateway_token(creds)
    assert exc.value.status_code == 401


async def test_require_gateway_token_missing_credentials_401(monkeypatch):
    monkeypatch.setattr(settings, "novelmind_gateway_token", "secret")
    with pytest.raises(HTTPException) as exc:
        await require_gateway_token(None)
    assert exc.value.status_code == 401


async def test_require_gateway_token_non_bearer_401(monkeypatch):
    monkeypatch.setattr(settings, "novelmind_gateway_token", "secret")
    creds = HTTPAuthorizationCredentials(scheme="Basic", credentials="secret")
    with pytest.raises(HTTPException) as exc:
        await require_gateway_token(creds)
    assert exc.value.status_code == 401


async def test_require_gateway_token_mismatch_401(monkeypatch):
    monkeypatch.setattr(settings, "novelmind_gateway_token", "expected")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
    with pytest.raises(HTTPException) as exc:
        await require_gateway_token(creds)
    assert exc.value.status_code == 401


async def test_require_gateway_token_matching_passes(monkeypatch):
    monkeypatch.setattr(settings, "novelmind_gateway_token", "expected-token")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="expected-token")
    assert await require_gateway_token(creds) is None


# ── require_agent_actor ──


async def test_require_agent_actor_no_token_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await require_agent_actor(1, _request(), None, db_session)
    assert exc.value.status_code == 401


async def test_require_agent_actor_uses_default_workspace_without_login(
    db_session, monkeypatch
):
    user = await _make_user(db_session, username="admin")
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "local_auto_login_username", "admin")

    actor = await require_agent_actor(1, _request(), None, db_session)

    assert actor.id == user.id


async def test_require_agent_actor_valid_jwt_returns_user(db_session, monkeypatch):
    user = await _make_user(db_session, username="jwtuser")
    token = create_access_token({"sub": str(user.id)})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    called = []

    async def fake_get_current_user(request, credentials, db):
        called.append(credentials)
        return user

    monkeypatch.setattr("app.core.security.get_current_user", fake_get_current_user)
    result = await require_agent_actor(5, _request(), creds, db_session)
    assert result is user
    assert called


async def test_require_agent_actor_invalid_jwt_falls_back_to_internal_token(
    monkeypatch,
):
    # Token starts with "ey" but is not a valid JWT → get_current_user raises 401
    # → falls through to the internal-token lookup.
    token = create_access_token({"sub": "1"})  # begins with "ey"
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    async def fake_get_current_user(request, credentials, db):
        raise HTTPException(status_code=401)

    monkeypatch.setattr("app.core.security.get_current_user", fake_get_current_user)

    # Simulate a matching SkillRun row.
    result_mock = MagicMock()
    run = SimpleNamespace(owner_id=11, novel_id=3)
    result_mock.scalars.return_value.first.return_value = run
    db_mock = AsyncMock()
    db_mock.execute.return_value = result_mock

    actor = await require_agent_actor(3, _request(), creds, db_mock)
    assert isinstance(actor, AgentActor)
    assert actor.id == 11
    assert actor.novel_id == 3


async def test_require_agent_actor_jwt_returns_none_falls_to_internal_token(
    monkeypatch,
):
    """get_current_user 返回 None（而非抛异常）时继续走 internal-token 分支。"""
    token = create_access_token({"sub": "1"})  # begins with "ey"
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    async def fake_get_current_user(request, credentials, db):
        return None

    monkeypatch.setattr("app.core.security.get_current_user", fake_get_current_user)

    result_mock = MagicMock()
    run = SimpleNamespace(owner_id=7, novel_id=3)
    result_mock.scalars.return_value.first.return_value = run
    db_mock = AsyncMock()
    db_mock.execute.return_value = result_mock

    actor = await require_agent_actor(3, _request(), creds, db_mock)
    assert isinstance(actor, AgentActor)
    assert actor.id == 7


async def test_require_agent_actor_internal_token_no_match_401():
    token = "internal-token-not-a-jwt"
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    result_mock = MagicMock()
    result_mock.scalars.return_value.first.return_value = None
    db_mock = AsyncMock()
    db_mock.execute.return_value = result_mock

    with pytest.raises(HTTPException) as exc:
        await require_agent_actor(3, _request(), creds, db_mock)
    assert exc.value.status_code == 401
