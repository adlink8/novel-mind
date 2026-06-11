"""
认证安全工具

提供密码哈希、JWT Token 生成与验证、当前用户依赖注入。
"""

from datetime import datetime, timedelta, timezone
import uuid
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.database import get_db
from app.models import User

# bcrypt 密码哈希上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer Token 认证方案
security_scheme = HTTPBearer(auto_error=False)
AUTH_COOKIE_NAME = "novelmind_session"
JWT_ISSUER = "novelmind"
JWT_AUDIENCE = "novelmind-web"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希密码是否匹配"""
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    """对明文密码进行 bcrypt 哈希"""
    return pwd_context.hash(password)


def validate_password_length(password: str) -> str:
    """Enforce bcrypt's 72-byte input limit without silently truncating UTF-8."""
    if len(password.encode("utf-8")) > 72:
        raise ValueError("密码的 UTF-8 编码不能超过 72 字节")
    return password


def validate_cookie_request_origin(request: Request) -> None:
    """Reject cross-site state changes authenticated only by the session cookie."""
    if request.method not in UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    allowed_origins = {value.rstrip("/") for value in settings.cors_origins}
    if not origin or origin.rstrip("/") not in allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无效的请求来源"
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
