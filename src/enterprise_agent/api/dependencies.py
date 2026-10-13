"""Trusted principal and Service factories; no identity from a tool parameter."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from enterprise_agent.database import Database
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.security import Principal
from enterprise_agent.services.auth import AuthService
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
    database = get_database(request)
    return await AuthService(database.session_factory, request.app.state.settings).authenticate(
        credentials.credentials
    )


def get_order_service(database: Annotated[Database, Depends(get_database)]) -> OrderService:
    return OrderService(database.session_factory)


def get_refund_service(
    request: Request, database: Annotated[Database, Depends(get_database)]
) -> RefundService:
    return RefundService(database.session_factory, clock=request.app.state.clock)


CurrentUser = Annotated[Principal, Depends(get_current_user)]
Orders = Annotated[OrderService, Depends(get_order_service)]
Refunds = Annotated[RefundService, Depends(get_refund_service)]
