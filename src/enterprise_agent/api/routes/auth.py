"""JSON login, Redis rate limiting, and a database-backed current user."""

import hashlib
import hmac

from fastapi import APIRouter, Request
from redis.exceptions import RedisError
from sqlalchemy import select

from enterprise_agent.api.dependencies import CurrentUser, get_database
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import User
from enterprise_agent.schemas.auth import LoginInput, TokenResponse, UserResponse
from enterprise_agent.services.auth import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


@router.post("/login", response_model=TokenResponse, summary="模拟账号登录")
async def login(payload: LoginInput, request: Request) -> TokenResponse:
    database = get_database(request)
    settings = request.app.state.settings
    redis_client = request.app.state.redis
    if redis_client is not None:
        address = request.client.host if request.client else "unknown"
        digest = hmac.new(
            settings.jwt_secret.get_secret_value().encode(),
            address.encode(),
            hashlib.sha256,
        ).hexdigest()
        try:
            attempts = await redis_client.eval(
                _LIMIT_SCRIPT,
                1,
                f"ea:login:{settings.app_env}:{digest}",
                settings.login_window_seconds,
            )
        except RedisError:
            raise BusinessError("rate_limiter_unavailable", "登录服务暂不可用", 503) from None
        if attempts > settings.login_max_attempts:
            raise BusinessError("login_rate_limited", "登录尝试过于频繁，请稍后重试", 429)
    return await AuthService(database.session_factory, settings).login(
        payload.username,
        payload.password.get_secret_value(),
    )


@router.get("/me", response_model=UserResponse, summary="当前已认证用户")
async def current_user(actor: CurrentUser, request: Request) -> UserResponse:
    async with get_database(request).session_factory() as session:
        user = await session.scalar(select(User).where(User.id == actor.user_id))
        return UserResponse.model_validate(user)
