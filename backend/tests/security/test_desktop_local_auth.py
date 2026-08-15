"""Desktop local session auth middleware tests (Phase 44-02 / T-44-02-02)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

pytestmark = pytest.mark.unit

from app.middleware.desktop_local_auth import (
    LOCAL_AUTH_ALGORITHMS,
    LOCAL_AUTH_BACKEND_AUDIENCE,
    LOCAL_AUTH_ISSUER,
    DesktopLocalAuthMiddleware,
    _bearer_token,
    _client_host,
)

TEST_SECRET = "test-local-auth-secret-0123456789abcdef"


def _mint_token(
    *,
    aud: str = LOCAL_AUTH_BACKEND_AUDIENCE,
    iss: str = LOCAL_AUTH_ISSUER,
    secret: str = TEST_SECRET,
    exp_offset: timedelta | None = None,
    iat_offset: timedelta | None = None,
    sid: str = "session-1",
    jti: str | None = None,
    extra: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    claims: dict = {
        "iss": iss,
        "aud": aud,
        "iat": int((now + (iat_offset or timedelta(0))).timestamp()),
        "exp": int((now + (exp_offset if exp_offset is not None else timedelta(minutes=5))).timestamp()),
        "jti": jti or "jti-1234",
        "sid": sid,
    }
    if extra:
        claims.update(extra)
    return jwt.encode(claims, secret, algorithm="HS256")


def _build_app(secret: str | None):
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/api/ping", ok)])
    app.add_middleware(DesktopLocalAuthMiddleware, secret=secret)
    return app


async def _client(secret: str | None, host: str = "127.0.0.1"):
    app = _build_app(secret)
    transport = ASGITransport(app=app, client=(host, 54321))
    return AsyncClient(transport=transport, base_url="http://test")


async def test_valid_session_token_succeeds():
    async with await _client(TEST_SECRET) as ac:
        resp = await ac.get(
            "/api/ping",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )
        assert resp.status_code == 200


async def test_missing_token_rejected():
    async with await _client(TEST_SECRET) as ac:
        resp = await ac.get("/api/ping")
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"


async def test_non_bearer_header_rejected():
    async with await _client(TEST_SECRET) as ac:
        resp = await ac.get(
            "/api/ping",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401


async def test_wrong_audience_rejected():
    async with await _client(TEST_SECRET) as ac:
        resp = await ac.get(
            "/api/ping",
            headers={"Authorization": f"Bearer {_mint_token(aud='novelmind-agent-local')}"},
        )
        assert resp.status_code == 401


async def test_expired_token_rejected():
    async with await _client(TEST_SECRET) as ac:
        token = _mint_token(exp_offset=timedelta(minutes=-10))
        resp = await ac.get(
            "/api/ping",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


async def test_wrong_secret_rejected():
    async with await _client(TEST_SECRET) as ac:
        token = _mint_token(secret="another-session-secret-0123456789")
        resp = await ac.get(
            "/api/ping",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


async def test_tampered_token_rejected():
    async with await _client(TEST_SECRET) as ac:
        token = _mint_token()
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        resp = await ac.get(
            "/api/ping",
            headers={"Authorization": f"Bearer {tampered}"},
        )
        assert resp.status_code == 401


async def test_old_restart_token_rejected():
    """重启后的令牌携带同一 secret 但 sid 属于旧会话 → 依赖服务侧校验；
    在中间件层面旧 sid 不应被静默接受的前提是签名/aud/exp 全通过。
    这里证明:更换 secret（模拟 main 每会话轮换）后旧令牌必然被拒。"""
    token = _mint_token(secret="old-session-secret-0123456789abcdef")
    async with await _client(TEST_SECRET) as ac:
        resp = await ac.get(
            "/api/ping",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


async def test_external_source_rejected_even_with_valid_token():
    """非回环来源持有有效令牌 → 拒绝（loopback source 校验）。"""
    async with await _client(TEST_SECRET, host="10.0.0.5") as ac:
        resp = await ac.get(
            "/api/ping",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )
        assert resp.status_code == 401


async def test_unconfigured_passes_through_without_token():
    """未配置密钥 → 放行（浏览器开发模式继续走现有 JWT/cookie 认证）。"""
    async with await _client(None) as ac:
        resp = await ac.get("/api/ping")
        assert resp.status_code == 200


async def test_helpers():
    assert _bearer_token("Bearer abc") == "abc"
    assert _bearer_token("Bearer  abc ") == "abc"
    assert _bearer_token("bearer abc") is None
    assert _bearer_token("Bearer") is None
    assert _bearer_token("") is None
    request = type("R", (), {"client": type("C", (), {"host": "127.0.0.1"})()})()
    assert _client_host(request) == "127.0.0.1"
    assert _client_host(type("R", (), {"client": None})()) == ""
