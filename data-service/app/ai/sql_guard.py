"""Static validation of model-generated SQL (layer 1 of 4).

The full defence-in-depth stack for natural-language queries:

  1. this guard     parse with sqlglot; exactly one read-only SELECT; only whitelisted
                    ai.* views; no dangerous functions; enforce a LIMIT
  2. database role  executed as ai_reader, which can only SELECT from the ai schema
  3. transaction    READ ONLY transaction, always rolled back
  4. resources      statement_timeout + fetch at most N rows

Any single layer failing closed is enough to stop a harmful query.
"""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

ALLOWED_VIEWS = {"orders", "order_lines", "customers", "products", "tickets", "employees", "customer_segments"}

# Statement / clause types that must never appear anywhere in the tree
FORBIDDEN_NODES = tuple(t for t in (
    getattr(exp, name, None) for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable", "Command",
        "Copy", "Grant", "Revoke", "Set", "Use", "Transaction", "Commit", "Rollback", "Into", "LoadData",
        "Pragma", "Analyze", "Lock",
    )) if t is not None)

# Functions that can read files, sleep, change settings, or reach other databases
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "set_config", "current_setting", "query_to_xml",
                               "table_to_xml", "cursor_to_xml", "database_to_xml", "xpath", "txid_", "file_")


class UnsafeSQL(ValueError):
    pass


def strip_fences(sql: str) -> str:
    sql = sql.strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", sql, re.S | re.I)
    if m:
        sql = m.group(1)
    return sql.strip().rstrip(";").strip()


def validate(sql: str, row_limit: int = 200) -> str:
    """Return a safe, normalised SQL string with a LIMIT, or raise UnsafeSQL."""
    sql = strip_fences(sql)
    if not sql:
        raise UnsafeSQL("empty query")
    if len(sql) > 8000:
        raise UnsafeSQL("query too long")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise UnsafeSQL(f"could not parse SQL: {str(exc).splitlines()[0][:200]}") from None
    if len(statements) != 1:
        raise UnsafeSQL("exactly one statement is allowed")
    stmt = statements[0]

    if not isinstance(stmt, exp.Query):           # Select / Union / Intersect / Except (+ WITH)
        raise UnsafeSQL(f"only SELECT queries are allowed (got {stmt.key.upper()})")
    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQL(f"{node.key.upper()} is not allowed")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise UnsafeSQL("row locking (FOR UPDATE/SHARE) is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        name = (table.name or "").lower()
        schema = (table.db or "").lower()
        if not name:
            raise UnsafeSQL("table functions are not allowed")
        if not schema and name in cte_names:
            continue
        if table.catalog:
            raise UnsafeSQL("cross-database references are not allowed")
        if schema not in ("", "ai") or name not in ALLOWED_VIEWS:
            raise UnsafeSQL(f"table '{(schema + '.') if schema else ''}{name}' is not available; "
                            f"use one of: {', '.join('ai.' + v for v in sorted(ALLOWED_VIEWS))}")
        if not schema:
            table.set("db", exp.to_identifier("ai"))   # qualify, so search_path tricks don't matter

    for fn in stmt.find_all(exp.Func):
        fname = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if fname.startswith(FORBIDDEN_FUNCTION_PREFIXES):
            raise UnsafeSQL(f"function {fname}() is not allowed")

    limit = stmt.args.get("limit")
    current = None
    if limit is not None:
        try:
            current = int(limit.expression.this) if isinstance(limit, exp.Limit) else None
        except (AttributeError, TypeError, ValueError):
            current = None
    if current is None or current > row_limit:
        stmt = stmt.limit(row_limit, copy=False) if current is None else _replace_limit(stmt, row_limit)
    return stmt.sql(dialect="postgres")


def _replace_limit(stmt: exp.Query, row_limit: int) -> exp.Query:
    stmt.set("limit", exp.Limit(expression=exp.Literal.number(row_limit)))
    return stmt
