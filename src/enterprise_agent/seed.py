"""Reproducible, entirely fictional ecommerce data for local development.

Run as the migration/initialization account. Seeding is append-only: an existing
demo_customer marks a completed seed transaction and prevents destructive resets.
"""

import argparse
import asyncio
import hashlib
import json
import random
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from enterprise_agent.config import PROJECT_ROOT, get_settings
from enterprise_agent.models import (
    AuditLog,
    KnowledgeDocument,
    Logistics,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    RefundPolicyVersion,
    RefundRequest,
    RefundStatus,
    SupportTicket,
    User,
    UserRole,
)
from enterprise_agent.schemas.business import PolicyResponse
from enterprise_agent.security import hash_password

_DOCUMENTS = [
    ("after-sales.md", "商品售后政策"),
    ("refund-and-return.md", "退款与退货规则"),
    ("logistics.md", "物流配送说明"),
    ("membership.md", "会员权益"),
    ("coupons.md", "平台优惠券规则"),
]
_COUNT_MODELS = (
    User,
    Product,
    Order,
    OrderItem,
    Logistics,
    RefundRequest,
    SupportTicket,
    KnowledgeDocument,
    AuditLog,
)


def _uuid(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://enterprise-agent.invalid/seed/{label}")


async def _counts(session: AsyncSession) -> dict[str, int]:
    result = {}
    for model in _COUNT_MODELS:
        result[model.__tablename__] = int(
            await session.scalar(select(func.count()).select_from(model))
        )
    return result


def _policy_snapshot(
    policy: RefundPolicyVersion,
    document: KnowledgeDocument,
    order: Order,
    items: list[OrderItem],
    checked_at: datetime,
) -> dict:
    source = PolicyResponse(
        id=policy.id,
        policy_code=policy.policy_code,
        version=policy.version,
        document_id=document.id,
        document_title=document.title,
        document_version=document.version,
        valid_from=policy.valid_from,
        valid_until=policy.valid_until,
        refund_window_days=policy.refund_window_days,
        refund_shipping=policy.refund_shipping,
        allow_before_delivery=policy.allow_before_delivery,
        excluded_categories=policy.excluded_categories,
    )
    return {
        "policy": source.model_dump(mode="json"),
        "facts": {
            "order_id": order.id,
            "user_id": order.user_id,
            "status": order.status.value,
            "paid_at": order.paid_at.astimezone(UTC).isoformat() if order.paid_at else None,
            "delivered_at": (
                order.delivered_at.astimezone(UTC).isoformat() if order.delivered_at else None
            ),
            "goods_paid_cents": order.goods_paid_cents,
            "shipping_paid_cents": order.shipping_paid_cents,
            "paid_total_cents": order.paid_total_cents,
            "items": [
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "category": item.category,
                    "quantity": item.quantity,
                    "paid_amount_cents": item.paid_amount_cents,
                }
                for item in items
            ],
        },
        "eligibility_checked_at": checked_at.astimezone(UTC).isoformat(),
        "decision": "eligible_pending_staff_review",
        "simulated_seed": True,
    }


async def _add_historical_refund(
    session: AsyncSession,
    order: Order,
    policy: RefundPolicyVersion,
    support: User,
    status: RefundStatus,
    as_of: datetime,
) -> None:
    # Seed histories are simulated business events, not evidence of external payments.
    created_at = min((order.delivered_at or order.paid_at) + timedelta(hours=1), as_of)
    reviewed = status in {
        RefundStatus.APPROVED,
        RefundStatus.REJECTED,
        RefundStatus.COMPLETED_SIMULATED,
    }
    reviewed_at = min(created_at + timedelta(hours=1), as_of) if reviewed else None
    completed_at = (
        min(reviewed_at + timedelta(hours=1), as_of)
        if status == RefundStatus.COMPLETED_SIMULATED
        else None
    )
    await session.flush()
    document = await session.get(KnowledgeDocument, policy.document_id)
    if document is None:
        raise ValueError("Seed policy source is missing")
    items = list(
        await session.scalars(
            select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.id)
        )
    )
    refund = RefundRequest(
        id=_uuid(f"refund/{order.order_no}"),
        order_id=order.id,
        applicant_id=order.user_id,
        amount_cents=order.goods_paid_cents,
        reason="change_mind",
        notes="模拟历史退款申请；未调用真实支付服务。",
        status=status,
        policy_version_id=policy.id,
        policy_snapshot=_policy_snapshot(policy, document, order, items, created_at),
        created_at=created_at,
        reviewer_id=support.id if reviewed else None,
        reviewed_at=reviewed_at,
        review_note="模拟客服审核记录" if reviewed else None,
        completed_at=completed_at,
    )
    session.add(refund)
    await session.flush()
    closed = status in {
        RefundStatus.REJECTED,
        RefundStatus.CANCELLED,
        RefundStatus.COMPLETED_SIMULATED,
    }
    session.add(
        SupportTicket(
            id=_uuid(f"ticket/{order.order_no}"),
            order_id=order.id,
            refund_request_id=refund.id,
            created_by=order.user_id,
            assigned_to=support.id if reviewed else None,
            status="closed"
            if closed
            else "approved"
            if status == RefundStatus.APPROVED
            else "open",
            created_at=created_at,
        )
    )
    events = [("refund.create", order.user_id, created_at)]
    if reviewed:
        review_action = "refund.reject" if status == RefundStatus.REJECTED else "refund.approve"
        events.append((review_action, support.id, reviewed_at))
    if status == RefundStatus.CANCELLED:
        events.append(("refund.cancel", order.user_id, created_at))
    if completed_at:
        events.append(("refund.complete_simulated", support.id, completed_at))
    for action, actor_id, event_at in events:
        session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                resource_type="refund",
                resource_id=str(refund.id),
                request_id="seed-demo",
                details={"order_no": order.order_no, "simulated_seed": True},
                created_at=event_at,
            )
        )


async def seed_demo(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    as_of: datetime,
    seed: int = 42,
    orders_count: int = 500,
    demo_password: str,
) -> dict[str, int]:
    """Atomically insert fictional data once, or report existing table counts.

    With the same seed, timestamp and empty migrated database, transaction facts
    are reproducible. Password salts are intentionally random. No existing rows
    are overwritten and no database/tables are created by this function.
    """
    if get_settings().app_env == "production":
        raise ValueError("Demo seeding is disabled in production")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    if not 5 <= orders_count <= 100_000:
        raise ValueError("orders_count must be between 5 and 100000")
    if len(demo_password) < 8:
        raise ValueError("Demo password must contain at least 8 characters")
    as_of = as_of.astimezone(UTC)
    rng = random.Random(seed)
    async with session_factory() as session, session.begin():
        # Serialize two concurrent seed commands without reserving user IDs.
        await session.execute(text("SELECT pg_advisory_xact_lock(617209130042)"))
        if await session.scalar(select(User.id).where(User.username == "demo_customer")):
            return await _counts(session)

        users = []
        for username, role in [
            ("demo_customer", UserRole.CUSTOMER),
            ("demo_other", UserRole.CUSTOMER),
            ("demo_support", UserRole.SUPPORT),
            ("demo_analyst", UserRole.ANALYST),
            ("demo_admin", UserRole.ADMIN),
        ] + [(f"synthetic_customer_{index:02d}", UserRole.CUSTOMER) for index in range(1, 21)]:
            user = User(
                username=username,
                password_hash=hash_password(demo_password),
                role=role,
                is_active=True,
                token_version=0,
                created_at=as_of - timedelta(days=100),
            )
            session.add(user)
            users.append(user)
        await session.flush()
        user_by_name = {user.username: user for user in users}
        customers = [user for user in users if user.role == UserRole.CUSTOMER]

        categories = ["electronics", "apparel", "home", "food", "beauty"]
        products = []
        for index in range(30):
            product = Product(
                sku=f"DEMO-{index + 1:04d}",
                name="演示无线耳机" if index == 0 else f"模拟商品 {index + 1:02d}",
                category=categories[index % len(categories)],
                price_cents=12900 if index == 0 else rng.randrange(1000, 40001, 100),
            )
            session.add(product)
            products.append(product)
        await session.flush()

        documents = []
        for filename, title in _DOCUMENTS:
            relative_path = Path("data") / "knowledge" / filename
            content = (PROJECT_ROOT / relative_path).read_bytes()
            document = KnowledgeDocument(
                id=_uuid(f"document/{filename}"),
                title=title,
                file_type="markdown",
                file_path=relative_path.as_posix(),
                checksum=hashlib.sha256(content).hexdigest(),
                version=1,
                visibility="public",
                status="uploaded",  # No embedding/index is claimed by Phase 2.
                index_generation=0,
                created_at=as_of - timedelta(days=365),
            )
            session.add(document)
            documents.append(document)
        await session.flush()
        policy = RefundPolicyVersion(
            policy_code="STANDARD_RETURN",
            version=1,
            document_id=documents[1].id,
            valid_from=as_of - timedelta(days=365),
            valid_until=None,
            refund_window_days=7,
            refund_shipping=False,
            allow_before_delivery=True,
            excluded_categories=["food"],
            created_at=as_of - timedelta(days=365),
        )
        session.add(policy)
        await session.flush()

        for index in range(orders_count):
            order_no = str(10086 + index)
            if index < 5:
                customer = user_by_name["demo_other" if index == 1 else "demo_customer"]
                days_ago = [5, 5, 18, 1, 8][index]
                created_at = as_of - timedelta(days=days_ago)
                status = OrderStatus.UNPAID if index == 3 else OrderStatus.DELIVERED
                paid_at = None if index == 3 else created_at + timedelta(hours=1)
                delivery_days = [2, 2, 14, 0, 5][index]
                delivered_at = as_of - timedelta(days=delivery_days) if index != 3 else None
                lines = [(products[0], 1, 0 if index == 3 else 12900)]
                shipping = 0 if index == 3 else 800
            else:
                customer = rng.choice(customers)
                created_at = as_of - timedelta(days=rng.randint(1, 89), hours=rng.randint(0, 23))
                status = rng.choices(list(OrderStatus), weights=[8, 12, 15, 60, 5], k=1)[0]
                paid_at = (
                    created_at + timedelta(minutes=15)
                    if status not in {OrderStatus.UNPAID, OrderStatus.CANCELLED}
                    else None
                )
                delivered_at = (
                    min(created_at + timedelta(days=rng.randint(1, 4)), as_of)
                    if status == OrderStatus.DELIVERED
                    else None
                )
                discount = rng.choice([0, 5, 10])
                lines = []
                for product in rng.sample(products, k=rng.randint(1, 3)):
                    quantity = rng.randint(1, 3)
                    gross = product.price_cents * quantity
                    paid = gross - gross * discount // 100 if paid_at else 0
                    lines.append((product, quantity, paid))
                shipping = rng.choice([0, 800, 1200]) if paid_at else 0
            goods_paid = sum(line[2] for line in lines)
            order = Order(
                order_no=order_no,
                user_id=customer.id,
                status=status,
                goods_paid_cents=goods_paid,
                shipping_paid_cents=shipping,
                paid_total_cents=goods_paid + shipping,
                created_at=created_at,
                paid_at=paid_at,
                delivered_at=delivered_at,
                updated_at=delivered_at or paid_at or created_at,
            )
            session.add(order)
            await session.flush()
            for product, quantity, paid in lines:
                session.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=product.id,
                        product_name=product.name,
                        category=product.category,
                        quantity=quantity,
                        unit_price_cents=product.price_cents,
                        paid_amount_cents=paid,
                    )
                )
            if status in {OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED}:
                events = [
                    {
                        "at": paid_at.isoformat(),
                        "status": "pending_dispatch",
                        "note": "模拟仓库已接单",
                    }
                ]
                if status in {OrderStatus.SHIPPED, OrderStatus.DELIVERED}:
                    events.append(
                        {
                            "at": (paid_at + timedelta(hours=2)).isoformat(),
                            "status": "in_transit",
                            "note": "模拟包裹运输中",
                        }
                    )
                if delivered_at:
                    events.append(
                        {"at": delivered_at.isoformat(), "status": "delivered", "note": "模拟签收"}
                    )
                session.add(
                    Logistics(
                        order_id=order.id,
                        tracking_no=f"SIM-{order_no}",
                        status={
                            OrderStatus.PAID: "pending_dispatch",
                            OrderStatus.SHIPPED: "in_transit",
                            OrderStatus.DELIVERED: "delivered",
                        }[status],
                        events=events,
                    )
                )
            seed_refund = index == 4 or (
                index > 4
                and index % 11 == 0
                and status == OrderStatus.DELIVERED
                and all(product.category != "food" for product, _, _ in lines)
                and delivered_at < as_of - timedelta(hours=3)
            )
            if seed_refund:
                refund_status = (
                    RefundStatus.COMPLETED_SIMULATED
                    if index == 4
                    else list(RefundStatus)[(index // 11) % len(RefundStatus)]
                )
                await _add_historical_refund(
                    session, order, policy, user_by_name["demo_support"], refund_status, as_of
                )
        await session.flush()
        counts = await _counts(session)
        session.add(
            AuditLog(
                actor_id=user_by_name["demo_admin"].id,
                action="seed.import",
                resource_type="demo_dataset",
                resource_id=f"seed-{seed}",
                request_id="seed-demo",
                details={
                    "seed": seed,
                    "as_of": as_of.isoformat(),
                    "counts": counts,
                    "simulated_seed": True,
                },
                created_at=as_of,
            )
        )
        await session.flush()
        return await _counts(session)


async def _run_cli(args: argparse.Namespace) -> dict[str, int]:
    settings = get_settings()
    if settings.app_env == "production":
        raise ValueError("Demo seeding is disabled in production")
    if settings.migration_database_url is None:
        raise ValueError("Set EA_MIGRATION_DATABASE_URL for the initialization account")
    try:
        url = make_url(settings.migration_database_url.get_secret_value())
    except Exception:
        raise ValueError("Initialization database URL is invalid") from None
    if url.get_backend_name() != "postgresql":
        raise ValueError("Demo seeding requires PostgreSQL")
    engine = create_async_engine(url.set(drivername="postgresql+psycopg"), hide_parameters=True)
    try:
        return await seed_demo(
            async_sessionmaker(engine, expire_on_commit=False),
            as_of=datetime.combine(args.as_of, time.min, tzinfo=UTC),
            seed=args.seed,
            orders_count=args.orders,
            demo_password=settings.demo_password.get_secret_value(),
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize a fictional local ecommerce dataset")
    parser.add_argument(
        "--as-of", type=date.fromisoformat, required=True, help="UTC anchor YYYY-MM-DD"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--orders", type=int, default=500)
    args = parser.parse_args()
    try:
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            counts = runner.run(_run_cli(args))
    except ValueError as error:
        parser.exit(2, f"Seed refused: {error}\n")
    except Exception:
        # Never print a driver traceback or password-bearing URL from a failed connection.
        parser.exit(1, "Seed failed; check migrated PostgreSQL and initialization permissions\n")
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
