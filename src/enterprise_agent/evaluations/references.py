"""Limited SQL semantics checks with independent, read-only base-table references.

Unsupported equivalent expressions remain manual_review. This is a dataset judge,
not a replacement for the application's SQL authorization or a general SQL prover.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from time import perf_counter

import sqlglot
from sqlglot import exp

from enterprise_agent.analytics.evaluation import _reference_result


def check(identifier, status, detail):
    return {"id": identifier, "status": status, "detail": detail}


def unalias(node):
    while isinstance(node, (exp.Alias, exp.Paren)):
        node = node.this
    return node


def number(node, value):
    node = unalias(node)
    try:
        return isinstance(node, exp.Literal) and not node.is_string and Decimal(node.this) == value
    except (InvalidOperation, TypeError):
        return False


def column(node, name):
    node = unalias(node)
    return isinstance(node, exp.Column) and node.name == name


def summed(node, name):
    node = unalias(node)
    return isinstance(node, exp.Sum) and column(node.this, name)


def date_literal(node):
    node = unalias(node)
    if isinstance(node, exp.Cast) and node.to.this == exp.DataType.Type.DATE:
        node = node.this
    if not isinstance(node, exp.Literal) or not node.is_string:
        return None
    try:
        return date.fromisoformat(node.this)
    except ValueError:
        return None


def date_contract(tree, start, end):
    """Accept a conjunction of exactly two DATE bounds, or an inclusive BETWEEN."""
    where = tree.args.get("where")
    if where is None:
        return False
    predicate = unalias(where.this)
    if isinstance(predicate, exp.Between):
        if (
            date_literal(predicate.args["low"]) is None
            or date_literal(predicate.args["high"]) is None
        ):
            return None
        return (
            column(predicate.this, "business_date")
            and date_literal(predicate.args["low"]) == start
            and date_literal(predicate.args["high"]) == end - timedelta(days=1)
        )
    if not isinstance(predicate, exp.And):
        return None
    terms = [unalias(predicate.this), unalias(predicate.expression)]
    lower = upper = False
    for term in terms:
        if not isinstance(term, (exp.GTE, exp.GT, exp.LT, exp.LTE)):
            return None
        if not column(term.this, "business_date"):
            return False
        value = date_literal(term.expression)
        if value is None:
            return None
        if (
            isinstance(term, exp.GTE)
            and value == start
            or isinstance(term, exp.GT)
            and value == start - timedelta(days=1)
        ):
            lower = True
        elif (
            isinstance(term, exp.LT)
            and value == end
            or isinstance(term, exp.LTE)
            and value == end - timedelta(days=1)
        ):
            upper = True
        else:
            return False
    return lower and upper


def refund_expression(node):
    node = unalias(node)
    if not isinstance(node, exp.Round) or not number(node.args.get("decimals"), Decimal(2)):
        return False
    ratio = unalias(node.this)
    if not isinstance(ratio, exp.Div):
        return False
    numerator, denominator = unalias(ratio.this), unalias(ratio.expression)
    if not isinstance(numerator, exp.Mul) or not isinstance(denominator, exp.Nullif):
        return False
    scale_ok = (
        number(numerator.this, Decimal(100))
        and summed(numerator.expression, "completed_refund_order_count")
    ) or (
        number(numerator.expression, Decimal(100))
        and summed(numerator.this, "completed_refund_order_count")
    )
    return (
        scale_ok
        and summed(denominator.this, "paid_order_count")
        and number(denominator.expression, Decimal(0))
    )


def period_sum(node, split, comparison):
    node = unalias(node)
    if not isinstance(node, exp.Sum):
        return False
    case = unalias(node.this)
    if not isinstance(case, exp.Case) or len(case.args.get("ifs", [])) != 1:
        return False
    branch = case.args["ifs"][0]
    condition = unalias(branch.this)
    return (
        isinstance(condition, comparison)
        and column(condition.this, "business_date")
        and date_literal(condition.expression) == split
        and column(branch.args["true"], "paid_order_count")
        and number(case.args.get("default"), Decimal(0))
    )


def sql_contract(reference_id, catalog, analysis):
    """Return reference SQL and an auditable conservative AST contract."""
    query = analysis["query"]
    tree = sqlglot.parse_one(query["normalized_sql"], read="postgres")
    selected = [unalias(node) for node in tree.expressions]
    aliases = [node.alias_or_name for node in tree.expressions]
    windows = catalog["date_windows"]
    if reference_id == "refund_rate_month_cohort":
        bounds = windows["this_month_to_date"]
    elif reference_id == "compare_complete_seven_day_periods":
        bounds = {
            "start_inclusive": windows["previous_complete_week"]["start_inclusive"],
            "end_exclusive": windows["latest_complete_week"]["end_exclusive"],
        }
    else:
        bounds = windows["last_seven_days_including_today"]
    start, end = (date.fromisoformat(bounds[key]) for key in ("start_inclusive", "end_exclusive"))
    table = "daily_product_sales" if reference_id == "product_sales_top_five" else "daily_orders"
    tables = list(tree.find_all(exp.Table))
    view_ok = (
        type(tree) is exp.Select
        and len(tables) == 1
        and tables[0].db == "ea_analytics"
        and tables[0].name == table
        and not tables[0].catalog
    )
    checks = [
        check(
            "read_views", "pass" if view_ok else "fail", "Expected one authorized aggregate view"
        ),
        check(
            "date_window",
            "pass"
            if date_contract(tree, start, end) is True
            else "fail"
            if date_contract(tree, start, end) is False
            else "manual_review",
            "Exact catalog DATE interval; other equivalent predicates need review",
        ),
    ]
    paid_date = "(o.paid_at AT TIME ZONE 'Asia/Shanghai')::date"
    base_where = (
        "o.paid_at IS NOT NULL AND o.status IN ('paid','shipped','delivered') "
        f"AND {paid_date} >= DATE '{start}' AND {paid_date} < DATE '{end}'"
    )
    roles, numeric, ordered = {}, [], False
    shape_ok = not query["truncated"] and len(aliases) == len(query["columns"])
    metric_ok = False
    if reference_id == "paid_sales_last_seven_days":
        shape_ok = shape_ok and len(selected) == 1 and not tree.args.get("group")
        metric_ok = shape_ok and summed(selected[0], "gross_revenue_cents")
        if len(aliases) == 1:
            roles[aliases[0]] = "sales_cents"
        numeric = ["sales_cents"]
        reference = (
            "SELECT SUM(o.paid_total_cents::numeric) AS sales_cents "
            f"FROM public.orders o WHERE {base_where}"
        )
    elif reference_id == "refund_rate_month_cohort":
        shape_ok = shape_ok and len(selected) == 1 and not tree.args.get("group")
        metric_ok = shape_ok and refund_expression(selected[0])
        if len(aliases) == 1:
            roles[aliases[0]] = "refund_rate_percent"
        numeric = ["refund_rate_percent"]
        reference = (
            "SELECT ROUND(100.0 * COUNT(DISTINCT r.order_id)::numeric / "
            "NULLIF(COUNT(DISTINCT o.id)::numeric, 0), 2) AS refund_rate_percent "
            "FROM public.orders o LEFT JOIN public.refund_requests r ON r.order_id = o.id "
            f"AND r.status = 'completed_simulated' WHERE {base_where}"
        )
    elif reference_id == "compare_complete_seven_day_periods":
        split = date.fromisoformat(windows["latest_complete_week"]["start_inclusive"])
        shape_ok = shape_ok and len(selected) == 2 and not tree.args.get("group")
        for alias, node in zip(aliases, selected, strict=True):
            if period_sum(node, split, exp.LT):
                roles[alias] = "previous_orders"
            elif period_sum(node, split, exp.GTE):
                roles[alias] = "latest_orders"
        metric_ok = set(roles.values()) == {"previous_orders", "latest_orders"}
        numeric = ["previous_orders", "latest_orders"]
        reference = (
            f"SELECT SUM(CASE WHEN {paid_date} < DATE '{split}' "
            "THEN 1::numeric ELSE 0::numeric END) "
            "AS previous_orders, "
            f"SUM(CASE WHEN {paid_date} >= DATE '{split}' THEN 1::numeric ELSE 0::numeric END) "
            f"AS latest_orders FROM public.orders o WHERE {base_where}"
        )
    elif reference_id == "product_sales_top_five":
        dimensions = {}
        for alias, node in zip(aliases, selected, strict=True):
            if isinstance(node, exp.Column) and node.name in {
                "product_id",
                "product_name",
                "category",
            }:
                roles[alias] = node.name
                dimensions[node.name] = f"i.{node.name}"
            elif summed(node, "goods_revenue_cents"):
                roles[alias] = "sales_cents"
        group = tree.args.get("group")
        grouped = (
            {node.name for node in group.expressions if isinstance(node, exp.Column)}
            if group
            else set()
        )
        shape_ok = (
            shape_ok
            and {"product_id", "product_name"} <= set(dimensions)
            and len(roles) == len(selected)
        )
        metric_ok = "sales_cents" in roles.values() and grouped == set(dimensions)
        order = tree.args.get("order")
        revenue_alias = next((key for key, value in roles.items() if value == "sales_cents"), None)
        rank = order.expressions[0] if order and order.expressions else None
        rank_ok = (
            rank is not None
            and rank.args.get("desc") is True
            and (column(rank.this, revenue_alias) or summed(rank.this, "goods_revenue_cents"))
        )
        limit = tree.args.get("limit")
        rank_ok = rank_ok and limit is not None and number(limit.expression, Decimal(5))
        checks.append(
            check(
                "ranking_limit",
                "pass" if rank_ok else "fail",
                "Revenue descending and LIMIT 5 required",
            )
        )
        if not dimensions:
            dimensions = {"product_id": "i.product_id", "product_name": "i.product_name"}
        names = list(dimensions)
        projection = ", ".join(f"{dimensions[name]} AS {name}" for name in names)
        grouping = ", ".join(dimensions.values())
        reference = (
            f"SELECT {projection}, SUM(i.paid_amount_cents::numeric) AS sales_cents "
            "FROM public.orders o JOIN public.order_items i ON i.order_id = o.id "
            f"WHERE {base_where} GROUP BY {grouping} ORDER BY sales_cents DESC, "
            + ", ".join(names)
            + " LIMIT 5"
        )
        numeric = [
            name for name in ("product_id", "sales_cents") if name in names or name == "sales_cents"
        ]
    else:
        raise ValueError("Unknown predefined SQL reference")
    checks.extend(
        [
            check(
                "metric_expression",
                "pass" if metric_ok else "manual_review",
                "Supported SUM/ratio/period expression; unsupported equivalents remain ungraded",
            ),
            check(
                "result_shape",
                "pass" if shape_ok else "manual_review",
                "Supported output grain and complete limited results",
            ),
        ]
    )
    return reference, roles, numeric, ordered, checks


async def build_sql_reference(reference_id, catalog, analysis, factory):
    reference, roles, numeric, ordered, checks = sql_contract(reference_id, catalog, analysis)
    started = perf_counter()
    expected = await _reference_result(factory, reference)
    expected.update(
        row_count=len(expected["rows"]),
        truncated=False,
        elapsed_ms=round((perf_counter() - started) * 1000, 3),
    )
    return {
        "reference_id": reference_id,
        "query": expected,
        "actual_column_roles": roles,
        "reference_column_roles": {name: name for name in expected["columns"]},
        "numeric_roles": numeric,
        "ordered": ordered,
        "ast_checks": checks,
    }
