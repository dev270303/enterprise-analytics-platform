"""Builds the schema description the model sees: the ai.* views, their columns and
COMMENTs (from the database catalog, so it never drifts from the real schema), plus
live facts such as the data's date range and the valid category/city names."""
from __future__ import annotations

import time

from app.db import connect

_CACHE: dict = {"at": 0.0, "text": None}
TTL_SECONDS = 600

COLUMNS_SQL = """
SELECT c.table_name, c.column_name, c.data_type,
       col_description(format('%I.%I', c.table_schema, c.table_name)::regclass, c.ordinal_position) AS column_comment,
       obj_description(format('%I.%I', c.table_schema, c.table_name)::regclass, 'pg_class')         AS table_comment
FROM information_schema.columns c
WHERE c.table_schema = 'ai'
ORDER BY c.table_name, c.ordinal_position
"""

TYPE_SHORT = {"character varying": "text", "timestamp without time zone": "timestamp", "double precision": "float",
              "smallint": "int", "integer": "int", "bigint": "int"}


def build() -> str:
    with connect() as conn:
        cols = conn.execute(COLUMNS_SQL).fetchall()
        facts = conn.execute("""
            SELECT (SELECT min(order_date) FROM orders) AS first_order, (SELECT max(order_date) FROM orders) AS last_order,
                   (SELECT string_agg(DISTINCT category, ', ' ORDER BY category) FROM products) AS categories,
                   (SELECT string_agg(name, ', ' ORDER BY name) FROM cities) AS cities,
                   (SELECT string_agg(DISTINCT segment_label, ', ') FROM customer_segments) AS rfm_segments
        """).fetchone()

    tables: dict[str, dict] = {}
    for c in cols:
        t = tables.setdefault(c["table_name"], {"comment": c["table_comment"], "cols": []})
        dtype = TYPE_SHORT.get(c["data_type"], c["data_type"])
        note = f"  -- {c['column_comment']}" if c["column_comment"] else ""
        t["cols"].append(f"    {c['column_name']} {dtype}{note}")

    lines = []
    for name, t in tables.items():
        lines.append(f"ai.{name}" + (f"  -- {t['comment']}" if t["comment"] else ""))
        lines.extend(t["cols"])
        lines.append("")
    lines += [
        "Facts about the data:",
        f"- Orders run from {facts['first_order']} to {facts['last_order']}. 'Today' / 'now' means {facts['last_order']} "
        f"(the latest data date), NOT the real current date. Express relative periods against "
        f"(SELECT max(order_date) FROM ai.orders).",
        f"- Product categories: {facts['categories']}.",
        f"- Cities: {facts['cities']}. Regions: North, South, East, West.",
        "- Currency is Indian Rupees (INR).",
    ]
    if facts["rfm_segments"]:
        lines.append(f"- RFM segments in ai.customer_segments.rfm_segment: {facts['rfm_segments']}.")
    return "\n".join(lines)


def get_schema_context(force: bool = False) -> str:
    now = time.time()
    if force or _CACHE["text"] is None or now - _CACHE["at"] > TTL_SECONDS:
        _CACHE["text"] = build()
        _CACHE["at"] = now
    return _CACHE["text"]
