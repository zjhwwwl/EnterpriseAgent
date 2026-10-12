"""Trusted principal and Service factories; no identity from a tool parameter."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from enterprise_agent.database import Database
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import User
from enterprise_agent.security import Principal, decode_access_token
from enterprise_agent.services.orders import OrderService
from enterprise_agent.services.refunds import RefundService

bearer = HTTPBearer(auto_error=False)


def get_database(request: Request) -> Database:
    database = request.app.state.database
    if database is None or not database.verified:
        raise BusinessError("database_unavailable", "业务数据库尚未就绪", 503)
    return database


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise BusinessError("unauthenticated", "请先登录", 401)
    user_id, version = decode_access_token(credentials.credentials, request.app.state.settings)
    database = get_database(request)
    async with database.session_factory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_active or user.token_version != version:
            raise BusinessError("unauthenticated", "登录已失效，请重新登录", 401)
        return Principal(user_id=user.id, role=user.role)


def get_order_service(database: Annotated[Database, Depends(get_database)]) -> OrderService:
    return OrderService(database.session_factory)


def get_refund_service(
    request: Request, database: Annotated[Database, Depends(get_database)]
) -> RefundService:
    return RefundService(database.session_factory, clock=request.app.state.clock)


CurrentUser = Annotated[Principal, Depends(get_current_user)]
Orders = Annotated[OrderService, Depends(get_order_service)]
Refunds = Annotated[RefundService, Depends(get_refund_service)]
