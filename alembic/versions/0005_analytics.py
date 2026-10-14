"""Expose aggregate ecommerce facts to dedicated read-only analytics roles.

Revision ID: 0005_analytics
Revises: 0004_agent_role

Revenue is paid gross revenue, before refunds. Completed refund metrics belong
to the original payment-date cohort, as observed at query time.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_analytics"
down_revision = "0004_agent_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA ea_analytics"))
    op.execute(sa.text("REVOKE ALL ON SCHEMA ea_analytics FROM PUBLIC"))
    op.execute(
        sa.text(
            "COMMENT ON SCHEMA ea_analytics IS "
            "'EnterpriseAgent analytics schema revision 0005_analytics'"
        )
    )
    op.execute(
        sa.text("""
        CREATE VIEW ea_analytics.daily_orders
        WITH (security_barrier = true, security_invoker = false) AS
        WITH completed_refunds AS (
            SELECT order_id, sum(amount_cents::numeric) AS completed_refund_cents
            FROM public.refund_requests
            WHERE status = 'completed_simulated'
            GROUP BY order_id
        )
        SELECT
            (orders.paid_at AT TIME ZONE 'Asia/Shanghai')::date AS business_date,
            count(*)::bigint AS paid_order_count,
            sum(orders.goods_paid_cents::numeric) AS goods_revenue_cents,
            sum(orders.shipping_paid_cents::numeric) AS shipping_revenue_cents,
            sum(orders.paid_total_cents::numeric) AS gross_revenue_cents,
            count(completed_refunds.order_id)::bigint AS completed_refund_order_count,
            coalesce(sum(completed_refunds.completed_refund_cents), 0::numeric)
                AS completed_refund_cents
        FROM public.orders AS orders
        LEFT JOIN completed_refunds ON completed_refunds.order_id = orders.id
        WHERE orders.paid_at IS NOT NULL
            AND orders.status IN ('paid', 'shipped', 'delivered')
        GROUP BY (orders.paid_at AT TIME ZONE 'Asia/Shanghai')::date
        """)
    )
    op.execute(
        sa.text("""
        CREATE VIEW ea_analytics.daily_product_sales
        WITH (security_barrier = true, security_invoker = false) AS
        SELECT
            (orders.paid_at AT TIME ZONE 'Asia/Shanghai')::date AS business_date,
            items.product_id::bigint AS product_id,
            items.product_name::text AS product_name,
            items.category::text AS category,
            sum(items.quantity)::bigint AS units_sold,
            sum(items.paid_amount_cents::numeric) AS goods_revenue_cents
        FROM public.orders AS orders
        JOIN public.order_items AS items ON items.order_id = orders.id
        WHERE orders.paid_at IS NOT NULL
            AND orders.status IN ('paid', 'shipped', 'delivered')
        GROUP BY (orders.paid_at AT TIME ZONE 'Asia/Shanghai')::date,
            items.product_id, items.product_name, items.category
        """)
    )
    # Cluster roles are initialized separately, without passwords in migrations.
    # Grant only the role dedicated to this database, never an API account.
    op.execute(
        sa.text("""
        DO $$ DECLARE reader_role text; BEGIN
            reader_role := CASE current_database()
                WHEN 'enterprise_agent' THEN 'ea_analytics_reader'
                WHEN 'enterprise_agent_test' THEN 'ea_test_analytics_reader'
                ELSE NULL END;
            IF reader_role IS NOT NULL
                AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = reader_role)
                AND has_database_privilege(reader_role, current_database(), 'CONNECT') THEN
                EXECUTE format('GRANT USAGE ON SCHEMA ea_analytics TO %I', reader_role);
                EXECUTE format('GRANT SELECT ON TABLE ea_analytics.daily_orders, '
                    'ea_analytics.daily_product_sales TO %I', reader_role);
            END IF;
        END $$;
        """)
    )


def downgrade() -> None:
    op.execute(sa.text("DROP VIEW ea_analytics.daily_product_sales"))
    op.execute(sa.text("DROP VIEW ea_analytics.daily_orders"))
    op.execute(sa.text("DROP SCHEMA ea_analytics"))
