"""Authentication facts come from PostgreSQL, not JWT role assertions."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import AuditLog, User
from enterprise_agent.request_context import get_request_id
from enterprise_agent.schemas.auth import TokenResponse, UserResponse
from enterprise_agent.security import (
    Principal,
    create_access_token,
    decode_access_token,
    dummy_password_hash,
    verify_password,
)


class AuthService:
    def __init__(self, factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.factory = factory
        self.settings = settings

    async def authenticate(self, token: str) -> Principal:
        """API and MCP resolve current permissions from the same SQL facts."""
        user_id, version = decode_access_token(token, self.settings)
        async with self.factory() as session:
            user = await session.scalar(select(User).where(User.id == user_id))
            if user is None or not user.is_active or user.token_version != version:
                raise BusinessError("unauthenticated", "登录已失效，请重新登录", 401)
            return Principal(user_id=user.id, role=user.role)

    async def login(self, username: str, password: str) -> TokenResponse:
        async with self.factory() as session, session.begin():
            user = await session.scalar(select(User).where(User.username == username))
            password_hash = (
                user.password_hash if user else await asyncio.to_thread(dummy_password_hash)
            )
            valid = await asyncio.to_thread(verify_password, password, password_hash)
            if not valid or user is None or not user.is_active:
                raise BusinessError("invalid_credentials", "账号或密码错误", 401)
            token = create_access_token(user.id, user.token_version, self.settings)
            session.add(
                AuditLog(
                    actor_id=user.id,
                    action="auth.login",
                    resource_type="user",
                    resource_id=str(user.id),
                    request_id=get_request_id(),
                    details={"outcome": "success"},
                )
            )
            return TokenResponse(
                access_token=token,
                expires_in=self.settings.jwt_expire_minutes * 60,
                user=UserResponse.model_validate(user),
            )
