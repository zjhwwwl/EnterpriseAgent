"""Password hashing and short-lived JWTs; user permissions are loaded from SQL."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import UserRole

_password_hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)
JWT_ISSUER = "enterprise-agent"
JWT_AUDIENCE = "enterprise-agent-api"


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    role: UserRole


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@lru_cache
def dummy_password_hash() -> str:
    return hash_password("fake-password-for-nonexistent-demo-user")


def create_access_token(user_id: int, token_version: int, settings: Settings) -> str:
    if settings.jwt_secret is None:
        raise BusinessError("authentication_unavailable", "认证尚未配置", 503)
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "ver": token_version,
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "jti": uuid4().hex,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(token: str, settings: Settings) -> tuple[int, int]:
    if settings.jwt_secret is None:
        raise BusinessError("authentication_unavailable", "认证尚未配置", 503)
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["sub", "ver", "iat", "exp", "iss", "aud", "jti"]},
        )
        subject = claims["sub"]
        version = claims["ver"]
        if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal():
            raise ValueError("invalid subject")
        if int(subject) <= 0 or type(version) is not int or version < 0:
            raise ValueError("invalid claims")
        return int(subject), version
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise BusinessError("unauthenticated", "登录已失效，请重新登录", 401) from None
