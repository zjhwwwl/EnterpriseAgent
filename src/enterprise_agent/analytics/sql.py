"""Fail-closed AST validation; SQL is never executed directly from a model output."""

import logging
import re
from dataclasses import dataclass
from datetime import date

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

from enterprise_agent.analytics.catalog import SCHEMA, VIEWS
from enterprise_agent.domain_errors import BusinessError

# SQLGlot's unsupported-syntax warnings interpolate the entire SQL input. Keep
# third-party parser diagnostics out of application logs and protocol streams;
# intentional failures are reported through static, typed BusinessError codes.
_parser_logger = logging.getLogger("sqlglot")
_parser_logger.handlers[:] = [logging.NullHandler()]
_parser_logger.propagate = False
_parser_logger.setLevel(logging.CRITICAL)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")

# Exact types deliberately exclude every new or unfamiliar AST construct. Functions
# such as pg_sleep, set_config, user-defined routines and schema-qualified calls
# cannot pass this allowlist, even when they occur inside a valid SELECT.
_ALLOWED_NODES = frozenset(
    {
        exp.Select,
        exp.From,
        exp.Table,
        exp.TableAlias,
        exp.Identifier,
        exp.Column,
        exp.Alias,
        exp.Literal,
        exp.Null,
        exp.Boolean,
        exp.Paren,
        exp.Star,
        exp.Where,
        exp.Group,
        exp.Having,
        exp.Order,
        exp.Ordered,
        exp.Limit,
        exp.Distinct,
        exp.Add,
        exp.Sub,
        exp.Mul,
        exp.Div,
        exp.Mod,
        exp.Neg,
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.And,
        exp.Or,
        exp.Not,
        exp.Is,
        exp.Between,
        exp.In,
        exp.Case,
        exp.If,
        exp.Sum,
        exp.Count,
        exp.Avg,
        exp.Min,
        exp.Max,
        exp.Round,
        exp.Nullif,
        exp.Coalesce,
        exp.Cast,
        exp.DataType,
        exp.DataTypeParam,
    }
)
_SELECT_ARGS = {"expressions", "from_", "where", "group", "having", "order", "limit", "distinct"}
_CAST_TYPES = {
    exp.DataType.Type.DATE,
    exp.DataType.Type.DECIMAL,
    exp.DataType.Type.INT,
    exp.DataType.Type.BIGINT,
    exp.DataType.Type.SMALLINT,
}


@dataclass(frozen=True)
class ValidatedQuery:
    normalized_sql: str
    view_name: str
    effective_limit: int


def _reject() -> None:
    raise BusinessError(
        "analytics_sql_rejected",
        "仅允许在授权分析视图上执行受限的单条 SELECT 查询。",
        422,
    ) from None


def _name(identifier: exp.Identifier) -> str:
    # PostgreSQL folds unquoted identifiers but preserves quoted ones.
    return identifier.name if identifier.args.get("quoted") else identifier.name.lower()


def _literal_integer(node: exp.Expression | None, maximum: int) -> int:
    if type(node) is not exp.Literal or node.is_string or not node.this.isdigit():
        _reject()
    value = int(node.this)
    if not 0 <= value <= maximum:
        _reject()
    return value


def validate_sql(sql: str, *, max_rows: int = 100, max_sql_chars: int = 8000) -> ValidatedQuery:
    """Validate, normalize and add a server-side fetch bound including a sentinel row.

    A caller's LIMIT at or below max_rows is preserved. Otherwise max_rows + 1
    lets the runtime truthfully report truncation without fetching a large result.
    """
    if type(sql) is not str or not sql.strip() or len(sql) > max_sql_chars:
        _reject()
    if not 1 <= max_rows <= 500 or not 1 <= max_sql_chars <= 32000:
        raise ValueError("Invalid analytics validation limits")
    try:
        statements = sqlglot.parse(sql, read="postgres", error_level=ErrorLevel.RAISE)
    except (SqlglotError, RecursionError, ValueError, TypeError):
        _reject()
    if len(statements) != 1 or type(statements[0]) is not exp.Select:
        _reject()
    tree = statements[0]
    if any(value and key not in _SELECT_ARGS for key, value in tree.args.items()):
        _reject()
    if not 1 <= len(tree.expressions) <= 20:
        _reject()
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1:
        _reject()
    table = tables[0]
    if (
        type(table.this) is not exp.Identifier
        or type(table.args.get("db")) is not exp.Identifier
        or table.args.get("catalog")
        or _name(table.args["db"]) != SCHEMA
        or _name(table.this) not in VIEWS
        or any(value and key not in {"this", "db", "alias"} for key, value in table.args.items())
    ):
        _reject()
    view = _name(table.this)
    alias = table.args.get("alias")
    if alias and (type(alias.this) is not exp.Identifier or alias.args.get("columns")):
        _reject()
    table_name = _name(alias.this) if alias else view
    aliases = {
        _name(projection.args["alias"])
        for projection in tree.expressions
        if type(projection) is exp.Alias and type(projection.args.get("alias")) is exp.Identifier
    }
    labels = [
        _name(projection.args["alias"])
        if type(projection) is exp.Alias
        else _name(projection.this)
        if type(projection) is exp.Column and type(projection.this) is exp.Identifier
        else projection.output_name
        for projection in tree.expressions
    ]
    if any(not label or len(label) > 63 for label in labels) or len(set(labels)) != len(labels):
        # Aggregates require an explicit label; duplicate result columns are ambiguous.
        _reject()

    for count, node in enumerate(tree.walk(), start=1):
        if count > 500:
            _reject()
        if type(node) not in _ALLOWED_NODES or node.comments:
            _reject()
        if type(node) is exp.Identifier:
            if not _IDENTIFIER.fullmatch(node.name):
                _reject()
            if not node.args.get("quoted"):
                node.set("this", node.name.lower())
        elif type(node) is exp.Column:
            if type(node.this) is not exp.Identifier or node.args.get("catalog"):
                _reject()
            if node.args.get("db") and _name(node.args["db"]) != SCHEMA:
                _reject()
            if node.args.get("table") and _name(node.args["table"]) != table_name:
                _reject()
            column_name = _name(node.this)
            if column_name not in VIEWS[view]:
                # PostgreSQL output aliases are usable in ORDER BY/GROUP BY.
                parent = node.parent
                while parent is not None and type(parent) not in {exp.Select, exp.Order, exp.Group}:
                    parent = parent.parent
                if column_name not in aliases or type(parent) not in {exp.Order, exp.Group}:
                    _reject()
        elif type(node) is exp.Star:
            if (
                type(node.parent) is not exp.Count
                or node.parent.this is not node
                or any(node.args.values())
            ):
                _reject()
        elif type(node) is exp.DataType:
            if node.this not in _CAST_TYPES or node.args.get("nested") or node.args.get("kind"):
                _reject()
            params = node.expressions
            if params:
                if node.this is not exp.DataType.Type.DECIMAL or len(params) > 2:
                    _reject()
                values = [_literal_integer(param.this, 30) for param in params]
                if values[0] == 0 or (len(values) == 2 and values[1] > min(values[0], 12)):
                    _reject()
        elif type(node) is exp.Cast and node.args["to"].this is exp.DataType.Type.DATE:
            if (
                type(node.this) is not exp.Literal
                or not node.this.is_string
                or not _DATE.fullmatch(node.this.this)
            ):
                _reject()
            try:
                date.fromisoformat(node.this.this)
            except ValueError:
                _reject()
        elif type(node) is exp.Literal:
            if len(node.this) > 512:
                _reject()
        elif type(node) is exp.In and not node.expressions:
            _reject()

    limit = tree.args.get("limit")
    effective_limit = max_rows + 1
    if limit:
        if any(value and key != "expression" for key, value in limit.args.items()):
            _reject()
        requested = _literal_integer(limit.expression, 1_000_000)
        effective_limit = min(requested, max_rows + 1)
    tree.limit(effective_limit, copy=False)
    normalized = tree.sql(dialect="postgres", pretty=False)
    if len(normalized) > max_sql_chars + 32:
        _reject()
    return ValidatedQuery(normalized, f"{SCHEMA}.{view}", effective_limit)
