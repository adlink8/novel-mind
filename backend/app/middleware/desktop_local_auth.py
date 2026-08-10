"""
桌面本地会话认证中间件（Phase 44-02 / D-44-04 / T-44-02-02）。

Electron main 进程为本地 FastAPI 铸造 audience/expiry 绑定的短命会话令牌，
HMAC 密钥经 NOVELMIND_LOCAL_AUTH_SECRET 注入受管进程环境。本中间件：

- 接受 `Authorization: Bearer <token>`，校验 iss / aud / exp / iat / sid / jti；
- aud 必须是 ``novelmind-desktop-local``（与 Agent Service 的
  ``novelmind-agent-local`` 严格分离，T-44-02-02 边界）；
- 来源必须是回环（127.0.0.1/::1）——LAN 来源即使持有有效令牌也拒绝
  （loopback source 校验，T-44-02-02）；
- 未配置密钥、缺失/无效/过期/错误 audience 的令牌一律 401 —— fail closed，
  绝不降级放行（D-44-04）；
- 令牌内容绝不写日志（V6 / T-44-02-01）。

浏览器开发模式不受影响：本中间件默认不启用，只有显式配置了
NOVELMIND_LOCAL_AUTH_SECRET 才启用（永远不是隐式绕过）；未配置时原样放行，
继续走现有 JWT/cookie 认证（桌面只在 main 注入密钥后才启用本地会话认证）。
"""

import logging
import time

from fastapi import Request
from jwt import PyJWTError
from jwt import decode as jwt_decode
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("novelmind")

LOCAL_AUTH_ISSUER = "novelmind-desktop-main"
LOCAL_AUTH_BACKEND_AUDIENCE = "novelmind-desktop-local"
LOCAL_AUTH_ALGORITHMS = ["HS256"]
# 本地令牌由 main 进程每 5 分钟铸造；服务侧时钟允许 60 秒偏斜（leeway）。
LOCAL_AUTH_LEEWAY_SECONDS = 60

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


class DesktopLocalAuthMiddleware(BaseHTTPMiddleware):
    """可选启用：配置了 local_auth_secret 时对本地桌面会话强制认证。"""

    def __init__(self, app, secret: str | None) -> None:
        super().__init__(app)
        self._secret = secret if isinstance(secret, str) and secret else None

    async def dispatch(self, request: Request, call_next):
        if self._secret is None:
            # 未配置本地会话认证 → 原样放行（浏览器开发模式继续走现有
            # JWT/cookie 认证；本地会话认证只在显式配置后强制，绝无隐式绕过）。
            return await call_next(request)
        if _client_host(request) not in _LOOPBACK_HOSTS:
            # 外部来源（非回环）：即使携带有效令牌也拒绝（loopback source 校验）。
            return _reject()
        auth_header = request.headers.get("authorization", "")
        token = _bearer_token(auth_header)
        if not token:
            return _reject()
        if not _verify_token(token, self._secret):
            return _reject()
        return await call_next(request)


def _client_host(request: Request) -> str:
    if request.client is None:
        return ""
    return request.client.host


def _bearer_token(authorization: str) -> str | None:
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer ") :].strip()
    return token or None


def _verify_token(token: str, secret: str) -> bool:
    """校验 audience/issuer/expiry/signature。任何失败 → False（fail closed）。"""
    try:
        payload = jwt_decode(
            token,
            secret,
            algorithms=LOCAL_AUTH_ALGORITHMS,
            audience=LOCAL_AUTH_BACKEND_AUDIENCE,
            issuer=LOCAL_AUTH_ISSUER,
            leeway=LOCAL_AUTH_LEEWAY_SECONDS,
            options={"require": ["aud", "exp", "iat", "jti", "sid", "iss"]},
        )
    except PyJWTError as exc:
        # 日志只含稳定的失败类别，绝不包含令牌/audience 片段。
        logger.debug("desktop local auth rejected: %s", type(exc).__name__)
        return False
    # iat 必须在过去（jwt 已按 leeway 校验 exp；iat 未来值视为无效）。
    iat = payload.get("iat")
    if not isinstance(iat, (int, float)) or iat > time.time() + LOCAL_AUTH_LEEWAY_SECONDS:
        return False
    return True


def _reject():
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=401,
        content={"detail": "本地会话认证失败"},
        headers={"WWW-Authenticate": "Bearer"},
    )
