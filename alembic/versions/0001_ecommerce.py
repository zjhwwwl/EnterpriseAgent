"""Create the ecommerce schema with explicit DDL and immutable published policies.

Revision ID: 0001_ecommerce
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_ecommerce"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "customer",
                "support",
                "analyst",
                "admin",
                name="user_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("token_version >= 0", name=op.f("ck_users_token_version_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint("price_cents >= 0", name=op.f("ck_products_price_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("sku", name=op.f("uq_products_sku")),
    )
    op.create_index("ix_products_category", "products", ["category"])
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("order_no", sa.String(40), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "unpaid",
                "paid",
                "shipped",
                "delivered",
                "cancelled",
                name="order_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("goods_paid_cents", sa.Integer(), nullable=False),
        sa.Column("shipping_paid_cents", sa.Integer(), nullable=False),
        sa.Column("paid_total_cents", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("goods_paid_cents >= 0", name=op.f("ck_orders_goods_paid_nonnegative")),
        sa.CheckConstraint(
            "shipping_paid_cents >= 0", name=op.f("ck_orders_shipping_paid_nonnegative")
        ),
        sa.CheckConstraint("paid_total_cents >= 0", name=op.f("ck_orders_paid_total_nonnegative")),
        sa.CheckConstraint(
            "paid_total_cents = goods_paid_cents + shipping_paid_cents",
            name=op.f("ck_orders_paid_total_matches"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_orders_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("order_no", name=op.f("uq_orders_order_no")),
    )
    op.create_index("ix_orders_user_created", "orders", ["user_id", "created_at"])
    op.create_index("ix_orders_status_created", "orders", ["status", "created_at"])
    op.create_index("ix_orders_paid_at", "orders", ["paid_at"])
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("paid_amount_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_items_quantity_positive")),
        sa.CheckConstraint(
            "unit_price_cents >= 0", name=op.f("ck_order_items_unit_price_nonnegative")
        ),
        sa.CheckConstraint(
            "paid_amount_cents >= 0", name=op.f("ck_order_items_paid_amount_nonnegative")
        ),
        sa.CheckConstraint(
            "paid_amount_cents <= quantity * unit_price_cents",
            name=op.f("ck_order_items_paid_amount_within_list_price"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="RESTRICT",
            name=op.f("fk_order_items_order_id_orders"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            ondelete="RESTRICT",
            name=op.f("fk_order_items_product_id_products"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_product_id", "order_items", ["product_id"])
    op.create_table(
        "logistics",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("tracking_no", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column(
            "events", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="RESTRICT",
            name=op.f("fk_logistics_order_id_orders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_logistics")),
        sa.UniqueConstraint("order_id", name=op.f("uq_logistics_order_id")),
        sa.UniqueConstraint("tracking_no", name=op.f("uq_logistics_tracking_no")),
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("file_type", sa.String(16), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("visibility", sa.String(16), server_default="public", nullable=False),
        sa.Column("status", sa.String(32), server_default="uploaded", nullable=False),
        sa.Column("index_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version > 0", name=op.f("ck_knowledge_documents_version_positive")),
        sa.CheckConstraint(
            "index_generation >= 0",
            name=op.f("ck_knowledge_documents_index_generation_nonnegative"),
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'internal')",
            name=op.f("ck_knowledge_documents_visibility_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_documents")),
    )
    op.create_index("ix_knowledge_documents_status", "knowledge_documents", ["status"])
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("index_generation", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "source_location",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "index_generation >= 0", name=op.f("ck_knowledge_chunks_index_generation_nonnegative")
        ),
        sa.CheckConstraint(
            "chunk_index >= 0", name=op.f("ck_knowledge_chunks_chunk_index_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
            name=op.f("fk_knowledge_chunks_document_id_knowledge_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        sa.UniqueConstraint(
            "document_id", "index_generation", "chunk_index", name="uq_chunk_version"
        ),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_table(
        "refund_policy_versions",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("policy_code", sa.String(60), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_window_days", sa.Integer(), server_default="7", nullable=False),
        sa.Column("refund_shipping", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "allow_before_delivery", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "excluded_categories",
            postgresql.JSONB(),
            server_default=sa.text("'[\"food\"]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_refund_policy_versions_version_positive")),
        sa.CheckConstraint(
            "refund_window_days >= 0",
            name=op.f("ck_refund_policy_versions_refund_window_nonnegative"),
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name=op.f("ck_refund_policy_versions_valid_period_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="RESTRICT",
            name=op.f("fk_refund_policy_versions_document_id_knowledge_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refund_policy_versions")),
        sa.UniqueConstraint("policy_code", "version", name="uq_refund_policy_code_version"),
        sa.UniqueConstraint("policy_code", "valid_from", name="uq_refund_policy_code_valid_from"),
    )
    op.create_index("ix_refund_policy_valid_from", "refund_policy_versions", ["valid_from"])
    op.create_table(
        "refund_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("applicant_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_review",
                "approved",
                "rejected",
                "cancelled",
                "completed_simulated",
                name="refund_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending_review",
            nullable=False,
        ),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_cents >= 0", name=op.f("ck_refund_requests_amount_nonnegative")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="RESTRICT",
            name=op.f("fk_refund_requests_order_id_orders"),
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_refund_requests_applicant_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_refund_requests_reviewer_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["policy_version_id"],
            ["refund_policy_versions.id"],
            ondelete="RESTRICT",
            name=op.f("fk_refund_requests_policy_version_id_refund_policy_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refund_requests")),
    )
    op.create_index("ix_refund_requests_order_id", "refund_requests", ["order_id"])
    op.create_index(
        "ix_refund_requests_applicant_created", "refund_requests", ["applicant_id", "created_at"]
    )
    op.create_index("ix_refund_requests_status", "refund_requests", ["status"])
    op.create_index(
        "uq_refund_active_order",
        "refund_requests",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_review', 'approved', 'completed_simulated')"),
    )
    op.create_table(
        "support_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("refund_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), server_default="open", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="RESTRICT",
            name=op.f("fk_support_tickets_order_id_orders"),
        ),
        sa.ForeignKeyConstraint(
            ["refund_request_id"],
            ["refund_requests.id"],
            ondelete="RESTRICT",
            name=op.f("fk_support_tickets_refund_request_id_refund_requests"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_support_tickets_created_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_support_tickets_assigned_to_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_tickets")),
        sa.UniqueConstraint("refund_request_id", name=op.f("uq_support_tickets_refund_request_id")),
    )
    op.create_index("ix_support_tickets_order_id", "support_tickets", ["order_id"])
    op.create_index("ix_support_tickets_status", "support_tickets", ["status"])
    op.create_table(
        "agent_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_agent_sessions_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_sessions")),
        sa.UniqueConstraint("thread_id", name=op.f("uq_agent_sessions_thread_id")),
    )
    op.create_index("ix_agent_sessions_user_id", "agent_sessions", ["user_id"])
    op.create_table(
        "agent_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column(
            "steps", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "token_usage", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            ondelete="RESTRICT",
            name=op.f("fk_agent_tasks_session_id_agent_sessions"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_agent_tasks_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_tasks")),
    )
    op.create_index("ix_agent_tasks_user_created", "agent_tasks", ["user_id", "created_at"])
    op.create_index("ix_agent_tasks_status", "agent_tasks", ["status"])
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("action_payload", postgresql.JSONB(), nullable=False),
        sa.Column("action_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "consumed",
                "rejected",
                "expired",
                name="approval_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_approval_requests_expires_after_created")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_approval_requests_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["agent_tasks.id"],
            ondelete="RESTRICT",
            name=op.f("fk_approval_requests_task_id_agent_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            ondelete="RESTRICT",
            name=op.f("fk_approval_requests_order_id_orders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
    )
    op.create_index("ix_approval_requests_user_status", "approval_requests", ["user_id", "status"])
    op.create_index("ix_approval_requests_order_id", "approval_requests", ["order_id"])
    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("refund_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_idempotency_records_actor_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["refund_request_id"],
            ["refund_requests.id"],
            ondelete="RESTRICT",
            name=op.f("fk_idempotency_records_refund_request_id_refund_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint(
            "actor_id", "operation", "key", name="uq_idempotency_actor_operation_key"
        ),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(60), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name=op.f("fk_audit_logs_actor_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_id", "created_at"])
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])
    op.execute(
        sa.text("""
        CREATE FUNCTION ea_refund_policy_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Published refund policy versions cannot be deleted'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'Publish a new refund policy version instead of modifying one'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER ea_refund_policy_immutable_trigger
        BEFORE UPDATE OR DELETE ON refund_policy_versions
        FOR EACH ROW EXECUTE FUNCTION ea_refund_policy_immutable();
    """)
    )
    # The API uses ordinary, non-owning roles. UPDATE(id) permits row locking
    # without permission to mutate order amounts, ownership, or status.
    op.execute(
        sa.text("""
        DO $$ DECLARE app_role text; BEGIN
            FOREACH app_role IN ARRAY ARRAY['ea_app', 'ea_test_app'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role)
                   AND has_database_privilege(app_role, current_database(), 'CONNECT') THEN
                    -- Remove legacy init defaults only for our two application roles.
                    -- Future revisions must explicitly grant their own project objects.
                    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                        'REVOKE ALL ON TABLES FROM %I', current_user, app_role);
                    EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                        'REVOKE ALL ON SEQUENCES FROM %I', current_user, app_role);
                    EXECUTE format('REVOKE ALL ON TABLE public.users, public.products, '
                        'public.orders, public.order_items, public.logistics, '
                        'public.knowledge_documents, public.knowledge_chunks, '
                        'public.refund_policy_versions, public.refund_requests, '
                        'public.support_tickets, public.agent_sessions, public.agent_tasks, '
                        'public.approval_requests, public.idempotency_records, '
                        'public.audit_logs, public.alembic_version FROM %I', app_role);
                    EXECUTE format('GRANT SELECT ON TABLE public.users, public.products, '
                        'public.orders, public.order_items, public.logistics, '
                        'public.knowledge_documents, public.knowledge_chunks, '
                        'public.refund_policy_versions, public.refund_requests, '
                        'public.support_tickets, public.agent_sessions, public.agent_tasks, '
                        'public.approval_requests, public.idempotency_records, '
                        'public.audit_logs, public.alembic_version TO %I', app_role);
                    EXECUTE format('GRANT INSERT, UPDATE, DELETE ON TABLE '
                        'public.approval_requests, public.refund_requests, '
                        'public.support_tickets, public.idempotency_records, '
                        'public.agent_tasks, public.agent_sessions TO %I', app_role);
                    EXECUTE format('GRANT INSERT ON TABLE public.audit_logs TO %I', app_role);
                    EXECUTE format('GRANT UPDATE(id) ON TABLE public.orders TO %I', app_role);
                    EXECUTE format('REVOKE ALL ON SEQUENCE public.users_id_seq, '
                        'public.products_id_seq, public.orders_id_seq, public.order_items_id_seq, '
                        'public.logistics_id_seq, public.refund_policy_versions_id_seq, '
                        'public.audit_logs_id_seq FROM %I', app_role);
                    EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE public.users_id_seq, '
                        'public.products_id_seq, public.orders_id_seq, public.order_items_id_seq, '
                        'public.logistics_id_seq, public.refund_policy_versions_id_seq, '
                        'public.audit_logs_id_seq TO %I', app_role);
                END IF;
            END LOOP;
        END $$;
    """)
    )


def downgrade() -> None:
    # Explicitly requested downgrade removes only tables owned by this revision.
    op.execute(sa.text("DROP TRIGGER ea_refund_policy_immutable_trigger ON refund_policy_versions"))
    op.execute(sa.text("DROP FUNCTION ea_refund_policy_immutable()"))
    op.drop_table("audit_logs")
    op.drop_table("idempotency_records")
    op.drop_table("approval_requests")
    op.drop_table("agent_tasks")
    op.drop_table("agent_sessions")
    op.drop_table("support_tickets")
    op.drop_table("refund_requests")
    op.drop_table("refund_policy_versions")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("logistics")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("products")
    op.drop_table("users")
