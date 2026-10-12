"""Order reads available to customers and designated support roles."""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import UserRole
from enterprise_agent.repositories.orders import OrderRepository
from enterprise_agent.schemas.business import (
    LogisticsResponse,
    OrderItemResponse,
    OrderPage,
    OrderResponse,
)
from enterprise_agent.security import Principal


def require_order_reader(actor: Principal) -> None:
    if actor.role not in (UserRole.CUSTOMER, UserRole.SUPPORT, UserRole.ADMIN):
        raise BusinessError(code="forbidden", message="当前角色无权访问订单。", status_code=403)


def validate_pagination(offset: int, limit: int) -> None:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise BusinessError(code="invalid_pagination", message="分页参数不合法。", status_code=422)


class OrderService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.sessions = sessions
        self.clock = clock

    async def list_orders(self, actor: Principal, *, offset: int = 0, limit: int = 20):
        require_order_reader(actor)
        validate_pagination(offset, limit)
        async with self.sessions() as session, session.begin():
            orders, total = await OrderRepository(session).list_visible(
                actor, offset=offset, limit=limit
            )
            return OrderPage(
                items=[OrderResponse.model_validate(order) for order in orders],
                total=total,
                offset=offset,
                limit=limit,
            )

    async def get_order(self, actor: Principal, order_no: str):
        require_order_reader(actor)
        async with self.sessions() as session, session.begin():
            order = await OrderRepository(session).get_visible(actor, order_no)
            if order is None:
                raise BusinessError(code="order_not_found", message="订单不存在。", status_code=404)
            return OrderResponse.model_validate(order)

    async def get_order_items(self, actor: Principal, order_no: str):
        require_order_reader(actor)
        async with self.sessions() as session, session.begin():
            repository = OrderRepository(session)
            order = await repository.get_visible(actor, order_no)
            if order is None:
                raise BusinessError(code="order_not_found", message="订单不存在。", status_code=404)
            return [
                OrderItemResponse.model_validate(item) for item in await repository.items(order.id)
            ]

    async def get_logistics(self, actor: Principal, order_no: str):
        require_order_reader(actor)
        async with self.sessions() as session, session.begin():
            repository = OrderRepository(session)
            order = await repository.get_visible(actor, order_no)
            if order is None:
                raise BusinessError(code="order_not_found", message="订单不存在。", status_code=404)
            logistics = await repository.logistics(order.id)
            if logistics is None:
                raise BusinessError(
                    code="logistics_not_found", message="订单暂无物流记录。", status_code=404
                )
            return LogisticsResponse(
                order_no=order.order_no,
                tracking_no=logistics.tracking_no,
                status=logistics.status,
                events=logistics.events,
            )
