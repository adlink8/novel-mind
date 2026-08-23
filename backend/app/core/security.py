"""
认证安全工具

提供密码哈希、JWT Token 生成与验证、当前用户依赖注入。
"""

from datetime import datetime, timedelta, timezone
import hashlib
import uuid
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.database import get_db
from app.models import User

# Use the maintained bcrypt API directly. Passlib 1.7.x probes the removed
# ``bcrypt.__about__`` attribute with current bcrypt releases, which produces
# noisy warnings and makes the CI authentication path unnecessarily fragile.
# Direct bcrypt calls still accept existing $2a/$2b/$2y hashes.
BCRYPT_ROUNDS = 12

# HTTP Bearer Token 认证方案
security_scheme = HTTPBearer(auto_error=False)
AUTH_COOKIE_NAME = "novelmind_session"
JWT_ISSUER = "novelmind"
JWT_AUDIENCE = "novelmind-web"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希密码是否匹配"""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("ascii")
        )
    except (UnicodeEncodeError, ValueError):
        # Malformed stored hashes fail closed instead of breaking login.
        return False


def hash_password(password: str) -> str:
    """对明文密码进行 bcrypt 哈希"""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("ascii")


def validate_password_length(password: str) -> str:
    """Enforce bcrypt's 72-byte input limit without silently truncating UTF-8."""
    if len(password.encode("utf-8")) > 72:
        raise ValueError("密码的 UTF-8 编码不能超过 72 字节")
    return password


def _request_origin(request: Request) -> str | None:
    """Best-effort browser origin from Origin, then Referer."""
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if not referer:
        return None
    # Referer: scheme://host[:port]/path → origin only
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(referer)
        if not parts.scheme or not parts.netloc:
            return None
        return f"{parts.scheme}://{parts.netloc}".rstrip("/")
    except Exception:
        return None


def validate_cookie_request_origin(request: Request) -> None:
    """Reject cross-site state changes authenticated only by the session cookie."""
    if request.method not in UNSAFE_METHODS:
        return
    origin = _request_origin(request)
    allowed_origins = {value.rstrip("/") for value in settings.cors_origins}
    if not origin or origin not in allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "无效的请求来源" + (f"（origin={origin!r}）" if settings.debug else "")
            ),
        )


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    创建 JWT Access Token。

    Args:
        data: 要编码到 Token 中的数据（通常包含 sub=user_id）
        expires_delta: 过期时间增量，默认使用配置中的 7 天
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    now = datetime.now(timezone.utc)
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "jti": uuid.uuid4().hex,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        }
    )
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm="HS256")
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """
    解码并验证 JWT Token。

    Returns:
        解码后的 payload 字典，或 None（验证失败时）
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )
        return payload
    except jwt.PyJWTError:
        return None


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """
    获取当前认证用户（可选认证）。

    如果请求未携带有效 Token，返回 None（允许匿名访问）。
    如果 Token 无效或过期，抛出 401 异常。
    """
    if not settings.auth_enabled:
        # Single-user desktop mode is deterministic: stale browser cookies or
        # sessionStorage JWTs must not switch the configured workspace owner.
        return await _get_default_workspace_user(db)

    token = (
        credentials.credentials
        if credentials
        else request.cookies.get(AUTH_COOKIE_NAME)
    )
    if not token:
        return None
    if not credentials:
        validate_cookie_request_origin(request)

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def require_user(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """
    强制要求认证用户。

    如果请求未携带 Token 或 Token 无效，抛出 401 异常。
    """
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


async def _get_default_workspace_user(db: AsyncSession) -> User:
    """Resolve the configured single-user workspace identity."""
    username = settings.local_auto_login_username.strip().lower()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="默认工作区用户未配置",
        )
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="默认工作区用户不存在或已被禁用",
        )
    return user


def _constant_time_equal(left: str, right: str) -> bool:
    """常量时间比较，避免时序侧信道泄露令牌内容。"""
    import secrets

    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


async def require_gateway_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> None:
    """
    智能体网关的服务到服务认证（25.2-02 Open Question 2 决策落地）。

    认证方式：agent-service 在请求头携带 ``Authorization: Bearer <token>``，
    与 ``settings.novelmind_gateway_token`` 做常量时间比较。

    安全属性（fail closed）:
      - 环境变量未配置 → 401（网关不可用，绝不降级放行）；
      - 缺失 / 非 Bearer / 不匹配 → 401，带 ``WWW-Authenticate: Bearer``；
      - 令牌内容绝不写日志、绝不下发浏览器（V6 / T-25.2-02-04）。

    备注（25.2-02 决策记录）:
      - 工具门面（/api/agent-tools）走端用户 JWT（现有 require_user），
        本依赖只用于网关；
      - 长时运行超过 JWT 过期的 per-run 短命内部令牌，由 25.2-03 的
        skill runtime 铸造，属 handoff 项，不在本阶段实现。
    """
    configured = settings.novelmind_gateway_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="网关令牌未配置",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少网关令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _constant_time_equal(credentials.credentials, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的网关令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )


class AgentActor:
    """internal_token 场景下的轻量调用方身份（agent-service 工具门面）。

    携带 owner_id（=id）与 novel_id（skill_run 绑定），兼容 User 的
    `id`/`is_superuser` 访问面，供 agent_tools 路由复用现有 owner 校验。
    """

    def __init__(self, *, id: int, novel_id: int, is_superuser: bool = False) -> None:
        self.id = id
        self.novel_id = novel_id
        self.is_superuser = is_superuser


async def require_agent_actor(
    novel_id: int,
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
    run_id: int | None = None,
) -> User | AgentActor:
    """agent_tools 门面认证：接受用户 JWT 或 per-run 内部令牌。

    - JWT：现有 `get_current_user` 语义（浏览器 / 直接 API 调用）；
    - internal_token：25.2-03 per-run 短命令牌。按 sha256(token) 匹配
      skill_runs.internal_token_hash，且 novel_id 必须与请求路径 novel_id 一致、
      状态为 queued/running（活跃 run）。命中返回 AgentActor（owner_id 即
      skill_run.owner_id，fail-closed：无活跃 run 一律 401）。
    """
    token = (
        credentials.credentials
        if credentials
        else request.cookies.get(AUTH_COOKIE_NAME)
    )
    if not token:
        if not settings.auth_enabled:
            return await _get_default_workspace_user(db)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not settings.auth_enabled:
        # Agent run tokens keep their explicit owner/novel binding. Any other
        # renderer token (including a stale user JWT or desktop-local token
        # already validated by middleware) resolves to the configured owner.
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        from app.models.agent_runtime import SkillRun

        predicates = [
            SkillRun.internal_token_hash == token_hash,
            SkillRun.novel_id == novel_id,
            SkillRun.status.in_(("queued", "running")),
        ]
        if run_id is not None:
            predicates.append(SkillRun.id == run_id)
        result = await db.execute(select(SkillRun).where(*predicates))
        run = result.scalars().first()
        if run is not None:
            return AgentActor(id=run.owner_id, novel_id=run.novel_id)
        return await _get_default_workspace_user(db)

    # Browser sessions carry the user JWT in the HttpOnly cookie. Internal
    # agent tokens are Bearer-only, so a cookie must be resolved as a user
    # before considering the internal-token branch.
    if credentials is None:
        user = await get_current_user(request, None, db)
        if user is not None:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1) 先试用户 JWT（直接 API Bearer 调用）。
    # 注意：token 以 "ey" 开头不代表是有效 JWT——random internal_token 偶发
    # 以 "ey" 开头（~0.02%）。get_current_user 对无效/过期 JWT 抛 401，这里
    # 捕获后继续尝试 internal-token 兜底，避免把内部令牌误判为坏 JWT。
    if token.startswith("ey"):
        try:
            user = await get_current_user(request, credentials, db)
            if user is not None:
                return user
        except HTTPException:
            # 无效/过期 JWT → 落到 internal_token 分支再判一次。
            pass
    # 2) internal_token（agent-service 工具门面）。
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    from app.models.agent_runtime import SkillRun

    predicates = [
        SkillRun.internal_token_hash == token_hash,
        SkillRun.novel_id == novel_id,
        SkillRun.status.in_(("queued", "running")),
    ]
    if run_id is not None:
        predicates.append(SkillRun.id == run_id)
    result = await db.execute(select(SkillRun).where(*predicates))
    run = result.scalars().first()
    if run is not None:
        return AgentActor(id=run.owner_id, novel_id=run.novel_id)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
