"""PostgreSQL ecommerce entities and database-enforced invariants.

Identity, money and policy checks live in the database as well as in Services.
Future RAG/Agent tables are persistence structures, not implemented AI features.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class UserRole(StrEnum):
    CUSTOMER = "customer"
    SUPPORT = "support"
    ANALYST = "analyst"
    ADMIN = "admin"


class OrderStatus(StrEnum):
    UNPAID = "unpaid"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class RefundStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    COMPLETED_SIMULATED = "completed_simulated"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    REJECTED = "rejected"
    EXPIRED = "expired"


def enum_column(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda members: [item.value for item in members],
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("token_version >= 0", name="token_version_nonnegative"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(enum_column(UserRole, "user_role"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql_text("true"))
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("price_cents >= 0", name="price_nonnegative"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(60), index=True)
    price_cents: Mapped[int] = mapped_column(Integer)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("goods_paid_cents >= 0", name="goods_paid_nonnegative"),
        CheckConstraint("shipping_paid_cents >= 0", name="shipping_paid_nonnegative"),
        CheckConstraint("paid_total_cents >= 0", name="paid_total_nonnegative"),
        CheckConstraint(
            "paid_total_cents = goods_paid_cents + shipping_paid_cents", name="paid_total_matches"
        ),
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index("ix_orders_status_created", "status", "created_at"),
        Index("ix_orders_paid_at", "paid_at"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus, "order_status"))
    goods_paid_cents: Mapped[int] = mapped_column(Integer)
    shipping_paid_cents: Mapped[int] = mapped_column(Integer)
    paid_total_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_nonnegative"),
        CheckConstraint("paid_amount_cents >= 0", name="paid_amount_nonnegative"),
        CheckConstraint(
            "paid_amount_cents <= quantity * unit_price_cents", name="paid_amount_within_list_price"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    product_name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(60))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    paid_amount_cents: Mapped[int] = mapped_column(Integer)


class Logistics(Base):
    __tablename__ = "logistics"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), unique=True)
    tracking_no: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(40))
    events: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=sql_text("'[]'::jsonb")
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("index_generation >= 0", name="index_generation_nonnegative"),
        CheckConstraint(
            "index_attempt_generation >= index_generation",
            name="index_attempt_generation_ordered",
        ),
        CheckConstraint("visibility IN ('public', 'internal')", name="visibility_allowed"),
        Index("ix_knowledge_documents_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200))
    file_type: Mapped[str] = mapped_column(String(16))
    file_path: Mapped[str] = mapped_column(String(500))
    checksum: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    visibility: Mapped[str] = mapped_column(String(16), default="public", server_default="public")
    status: Mapped[str] = mapped_column(String(32), default="uploaded", server_default="uploaded")
    index_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    index_attempt_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active_index_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    indexing_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    index_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_fingerprint: Mapped[str | None] = mapped_column(String(128))
    index_error_code: Mapped[str | None] = mapped_column(String(64))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "index_generation", "chunk_index", name="uq_chunk_version"),
        CheckConstraint("index_generation >= 0", name="index_generation_nonnegative"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    index_generation: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    source_location: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )


class RefundPolicyVersion(Base):
    __tablename__ = "refund_policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_code", "version", name="uq_refund_policy_code_version"),
        UniqueConstraint("policy_code", "valid_from", name="uq_refund_policy_code_valid_from"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("refund_window_days >= 0", name="refund_window_nonnegative"),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from", name="valid_period_ordered"
        ),
        Index("ix_refund_policy_valid_from", "valid_from"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    policy_code: Mapped[str] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="RESTRICT")
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_window_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    refund_shipping: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false")
    )
    allow_before_delivery: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sql_text("true")
    )
    excluded_categories: Mapped[list[str]] = mapped_column(
        JSONB, default=lambda: ["food"], server_default=sql_text("'[\"food\"]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefundRequest(Base):
    __tablename__ = "refund_requests"
    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="amount_nonnegative"),
        Index("ix_refund_requests_applicant_created", "applicant_id", "created_at"),
        Index("ix_refund_requests_status", "status"),
        Index(
            "uq_refund_active_order",
            "order_id",
            unique=True,
            postgresql_where=sql_text(
                "status IN ('pending_review', 'approved', 'completed_simulated')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(40))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[RefundStatus] = mapped_column(
        enum_column(RefundStatus, "refund_status"),
        default=RefundStatus.PENDING_REVIEW,
        server_default=RefundStatus.PENDING_REVIEW.value,
    )
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey("refund_policy_versions.id", ondelete="RESTRICT")
    )
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    refund_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refund_requests.id", ondelete="RESTRICT"), unique=True
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(
        String(32), default="open", server_default="open", index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    thread_id: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (Index("ix_agent_tasks_user_created", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="RESTRICT")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    task_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), index=True)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=sql_text("'[]'::jsonb")
    )
    token_usage: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index("ix_approval_requests_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="RESTRICT"))
    operation: Mapped[str] = mapped_column(String(60))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    action_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    action_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_column(ApprovalStatus, "approval_status"),
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "key", name="uq_idempotency_actor_operation_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    operation: Mapped[str] = mapped_column(String(60))
    key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    refund_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refund_requests.id", ondelete="RESTRICT")
    )
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
