"""Order queries with ownership included in the SQL predicate."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.models import Logistics, Order, OrderItem, UserRole
from enterprise_agent.security import Principal


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def visible_statement(actor: Principal):
        statement = select(Order)
        if actor.role == UserRole.CUSTOMER:
            statement = statement.where(Order.user_id == actor.user_id)
        return statement

    async def get_visible(self, actor: Principal, order_no: str, *, lock: bool = False):
        statement = self.visible_statement(actor).where(Order.order_no == order_no)
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_by_id(self, order_id: int, *, lock: bool = False):
        statement = select(Order).where(Order.id == order_id)
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_visible(self, actor: Principal, *, offset: int, limit: int):
        statement = self.visible_statement(actor)
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        result = await self.session.scalars(
            statement.order_by(Order.created_at.desc(), Order.id.desc()).offset(offset).limit(limit)
        )
        return list(result), total or 0

    async def items(self, order_id: int) -> list[OrderItem]:
        result = await self.session.scalars(
            select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
        )
        return list(result)

    async def logistics(self, order_id: int) -> Logistics | None:
        return await self.session.scalar(select(Logistics).where(Logistics.order_id == order_id))
