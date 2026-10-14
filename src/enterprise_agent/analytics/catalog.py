"""The complete public analytics catalog and its metric definitions."""

SCHEMA = "ea_analytics"
SCHEMA_MARKER = "EnterpriseAgent analytics schema revision 0005_analytics"

VIEWS: dict[str, dict[str, str]] = {
    "daily_orders": {
        "business_date": "DATE",
        "paid_order_count": "BIGINT",
        "goods_revenue_cents": "NUMERIC",
        "shipping_revenue_cents": "NUMERIC",
        "gross_revenue_cents": "NUMERIC",
        "completed_refund_order_count": "BIGINT",
        "completed_refund_cents": "NUMERIC",
    },
    "daily_product_sales": {
        "business_date": "DATE",
        "product_id": "BIGINT",
        "product_name": "TEXT",
        "category": "TEXT",
        "units_sold": "BIGINT",
        "goods_revenue_cents": "NUMERIC",
    },
}

METRIC_DEFINITIONS = {
    "paid_sales": (
        "按 Asia/Shanghai 的支付日期统计，订单 paid_at 非空且状态为 paid、shipped、"
        "delivered；gross_revenue_cents 为实际支付金额，含运费、不扣退款，单位分。"
    ),
    "refund_rate": (
        "所选支付日期订单群截至查询时 completed_simulated 的退款订单数 / 已支付订单数；"
        "不是所选期间发生的退款事件率；分母为零时返回 NULL。"
    ),
    "order_count": ("按 Asia/Shanghai 支付日期计算的已支付订单数，不包含未支付或已取消订单。"),
    "product_sales": (
        "按支付日期统计商品实付金额，单位分，不含运费、不扣退款；名称和类目"
        "使用订单明细快照，历史名称不同会分组展示。"
    ),
}


def catalog_text() -> str:
    """A small, explicit schema description for the SQL planner's system prompt."""
    tables = [
        f"{SCHEMA}.{name}: " + ", ".join(f"{column} {kind}" for column, kind in columns.items())
        for name, columns in VIEWS.items()
    ]
    metrics = [f"{name}: {definition}" for name, definition in METRIC_DEFINITIONS.items()]
    return "\n".join(tables + metrics)
