"""
认证 API — 用户注册、登录、Token 管理
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, text, update

from app.core.database import get_db
from app.config import settings
from app.core.security import (
    AUTH_COOKIE_NAME,
    create_access_token,
    hash_password,
    require_user,
    validate_password_length,
    verify_password,
)
from app.models import AIModelConfig, Novel, User

router = APIRouter()


class RegisterRequest(BaseModel):
    """用户注册请求"""

    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: str = Field(
        ..., min_length=3, max_length=100, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    password: str = Field(..., min_length=8, max_length=100, description="密码")
    # 仅当服务为「引导管理员」状态时使用：首次部署时配置了
    # NOVELMIND_BOOTSTRAP_ADMIN_TOKEN 的情况下，首个注册用户必须携带匹配的
    # 一次性 bootstrap token 才能成为 superuser。普通注册可省略。
    bootstrap_token: str = Field(default="", max_length=200, description="引导 token")

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_length(value)


class LoginRequest(BaseModel):
    """用户登录请求"""

    username: str = Field(..., description="用户名")
    password: str = Field(..., max_length=100, description="密码")

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_length(value)


class TokenResponse(BaseModel):
    """登录成功响应"""

    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


class UserResponse(BaseModel):
    """用户信息响应"""

    id: int
    username: str
    email: str
    is_active: bool


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    用户注册。

    检查用户名和邮箱是否已存在，对密码进行 bcrypt 哈希后存入数据库。
    """
    # 检查用户名是否已存在
    username = data.username.strip().lower()
    email = data.email.strip().lower()
    if db.bind and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(73921401)"))
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已被使用")

    # 检查邮箱是否已存在
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    is_bootstrap_admin = (
        await db.scalar(select(func.count(User.id)).where(User.is_active.is_(True)))
    ) == 0
    if is_bootstrap_admin:
        configured = settings.bootstrap_admin_token
        if configured:
            # 配置了一次性 bootstrap token：首个注册用户必须携带匹配 token，
            # 否则拒绝。常量时间比较避免时序侧信道。
            if not data.bootstrap_token or not secrets.compare_digest(
                data.bootstrap_token.encode("utf-8"), configured.encode("utf-8")
            ):
                raise HTTPException(
                    status_code=403, detail="bootstrap token 缺失或不匹配"
                )
        elif not settings.debug:
            # 生产模式（debug=False）且未配置 bootstrap token：fail-closed，
            # 禁止公开注册抢占超级管理员；运维必须先配置 token 或离线预置管理员。
            raise HTTPException(
                status_code=403,
                detail="bootstrap admin 未初始化：需配置 NOVELMIND_BOOTSTRAP_ADMIN_TOKEN 或离线预置管理员",
            )
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(data.password),
        is_superuser=is_bootstrap_admin,
    )
    db.add(user)
    await db.flush()
    if is_bootstrap_admin:
        legacy_owner = await db.scalar(
            select(User.id).where(User.username == "legacy-owner")
        )
        if legacy_owner:
            await db.execute(
                update(Novel)
                .where(Novel.owner_id == legacy_owner)
                .values(owner_id=user.id)
            )
            await db.execute(
                update(AIModelConfig)
                .where(AIModelConfig.owner_id == legacy_owner)
                .values(owner_id=user.id)
            )
    await db.refresh(user)

    return UserResponse(
        id=user.id, username=user.username, email=user.email, is_active=user.is_active
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)
):
    """
    用户登录。

    验证用户名和密码，成功后返回 JWT Access Token。
    """
    result = await db.execute(
        select(User).where(User.username == data.username.strip().lower())
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="用户已被禁用")

    access_token = create_access_token(data={"sub": str(user.id)})
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=access_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return TokenResponse(
        access_token=access_token,
        user_id=user.id,
        username=user.username,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(require_user)):
    """获取当前登录用户信息"""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
    )
