"""Natural-language -> SQL -> answer.

  question
     |  LLM call 1: route + SQL (JSON), with schema context, business rules, few-shot examples
     v
  sql_guard.validate()          static checks, LIMIT
     v
  execute_readonly()            ai_reader role, READ ONLY txn, statement_timeout, row cap
     |  on error: one self-correction attempt (error message fed back to the model)
     v
  LLM call 2: plain-English summary of the result rows
     v
  {answer, sql, columns, rows, chart}
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import logging
from dataclasses import dataclass, field

import psycopg
from psycopg.rows import tuple_row

from app.ai import sql_guard
from app.ai.llm import LLM, LLMError, extract_json
from app.ai.schema_context import get_schema_context
from app.config import get_settings
from app.db import connect

log = logging.getLogger(__name__)
logging.getLogger("sqlglot").setLevel(logging.ERROR)

_ROLE_ISOLATION: dict = {"available": None}

SYSTEM_PROMPT = """You are a senior data analyst for an Indian electronics and office-supplies company.
You translate business questions into a single PostgreSQL query over the views below.

{schema}

Rules:
- Use ONLY the ai.* views listed above. Always prefix them with "ai.".
- Revenue = SUM(total_amount) from ai.orders, or SUM(line_revenue) from ai.order_lines, counting only rows WHERE is_revenue.
- Relative periods ("last 6 months", "this year", "last week") are relative to the latest data date, e.g.
  order_date > (SELECT max(order_date) FROM ai.orders) - INTERVAL '6 months'.
- Group monthly data with date_trunc('month', order_date)::date AS month and order chronologically.
- For "top N" use ORDER BY ... DESC LIMIT N. Round money to 0 decimals and percentages to 1 decimal.
- Give columns readable snake_case aliases (e.g. revenue, orders, avg_order_value, breach_rate_pct).
- Return one row per category/period, not raw order rows, unless the user asks for a list.
- Read-only: never modify data.

Decide the route:
- "sql" for questions answerable from the data.
- "docs" for questions about company policies, rules, SLAs as policy, warranties, refunds as policy, procedures.
- "unsupported" for anything else (chit-chat, other companies, personal data, requests to change data).

Reply with JSON only:
{{"route": "sql" | "docs" | "unsupported", "sql": "<query or empty>", "chart": "line" | "bar" | "table" | "number",
  "title": "<short chart title>", "reason": "<one short sentence>"}}

Examples:
Q: Monthly revenue in the South region this year
{{"route": "sql", "sql": "SELECT date_trunc('month', order_date)::date AS month, round(sum(total_amount)) AS revenue FROM ai.orders WHERE is_revenue AND region = 'South' AND order_date >= date_trunc('year', (SELECT max(order_date) FROM ai.orders)) GROUP BY 1 ORDER BY 1", "chart": "line", "title": "South region revenue by month", "reason": "Monthly revenue trend"}}
Q: Top 5 products by revenue in Delhi during the last 6 months
{{"route": "sql", "sql": "SELECT product_name, round(sum(line_revenue)) AS revenue, sum(quantity) AS units FROM ai.order_lines WHERE is_revenue AND city = 'Delhi' AND order_date > (SELECT max(order_date) FROM ai.orders) - INTERVAL '6 months' GROUP BY product_name ORDER BY revenue DESC LIMIT 5", "chart": "bar", "title": "Top 5 products in Delhi, last 6 months", "reason": "Product ranking"}}
Q: What is the refund policy for orders above 50,000?
{{"route": "docs", "sql": "", "chart": "table", "title": "", "reason": "Policy question"}}
"""

SUMMARY_PROMPT = """You are a business analyst. Answer the user's question in 2-4 sentences using ONLY the query result.
- Lead with the direct answer and the key number(s). Mention one notable pattern if there is one.
- Format money in Indian style: ₹1.25 Cr (crore = 10,000,000), ₹4.8 L (lakh = 100,000), or ₹12,500.
- Do not mention SQL, tables or columns. Do not invent data that is not in the result.
- If the result is empty, say no matching data was found and suggest how to rephrase.

Question: {question}
Rows returned: {row_count}{truncated}
Result (CSV):
{csv}
"""


@dataclass
class SqlAnswer:
    route: str
    answer: str = ""
    sql: str | None = None
    title: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    chart: dict | None = None
    attempts: int = 0
    status: str = "OK"
    error: str | None = None


def _json_value(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return round(v.total_seconds() / 3600, 2)
    return v


def role_isolation_available() -> bool | None:
    return _ROLE_ISOLATION["available"]


def execute_readonly(sql: str) -> tuple[list[str], list[list], bool]:
    """Run validated SQL as ai_reader in a READ ONLY transaction that is always rolled back."""
    s = get_settings()
    with connect() as conn:
        conn.read_only = True
        try:
            conn.execute(f"SET LOCAL statement_timeout = {int(s.ai_statement_timeout_ms)}")
            conn.execute("SET LOCAL search_path = ai")
            if _ROLE_ISOLATION["available"] is not False:
                try:
                    with conn.transaction():
                        conn.execute("SET LOCAL ROLE ai_reader")
                    _ROLE_ISOLATION["available"] = True
                except psycopg.Error as exc:
                    _ROLE_ISOLATION["available"] = False
                    log.warning("ai_reader role unavailable, relying on READ ONLY + guard: %s", exc)
            cur = conn.cursor(row_factory=tuple_row)      # tuples: duplicate column names are possible
            cur.execute(sql)
            columns = [d.name for d in cur.description or []]
            raw = cur.fetchmany(s.ai_row_limit + 1)
            truncated = len(raw) > s.ai_row_limit
            rows = [[_json_value(v) for v in r] for r in raw[: s.ai_row_limit]]
            return columns, rows, truncated
        finally:
            conn.rollback()


def suggest_chart(columns: list[str], rows: list[list], hint: str | None, title: str | None) -> dict | None:
    if not rows or not columns:
        return None
    numeric = [i for i, _ in enumerate(columns) if all(isinstance(r[i], (int, float)) or r[i] is None for r in rows)]
    if len(rows) == 1 and len(numeric) >= 1 and len(columns) <= 3:
        return {"type": "number", "title": title, "value_index": numeric[0]}
    if len(columns) < 2 or not numeric:
        return {"type": "table", "title": title}
    label_idx = next((i for i in range(len(columns)) if i not in numeric), None)
    if label_idx is None:
        return {"type": "table", "title": title}
    values = [i for i in numeric if i != label_idx][:3]
    first = str(rows[0][label_idx])
    looks_temporal = (len(first) >= 7 and first[:4].isdigit() and first[4] == "-") or columns[label_idx] in ("month", "week", "day", "date", "year", "quarter")
    if looks_temporal and len(rows) >= 3:
        kind = "line"
    elif len(rows) <= 30:
        kind = "bar"
    else:
        return {"type": "table", "title": title}
    if hint in ("line", "bar") and not (hint == "line" and not looks_temporal):
        kind = hint
    return {"type": kind, "title": title, "label_index": label_idx, "value_indexes": values}


def _to_csv(columns, rows, limit=30) -> str:
    def cell(v):
        text = "" if v is None else str(v)
        return f'"{text}"' if "," in text else text
    out = [",".join(columns)] + [",".join(cell(v) for v in r) for r in rows[:limit]]
    return "\n".join(out)


def generate_sql(llm: LLM, question: str, feedback: list[dict] | None = None) -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(schema=get_schema_context())},
                {"role": "user", "content": f"Q: {question}"}]
    if feedback:
        messages.extend(feedback)
    return extract_json(llm.chat(messages))


def answer(llm: LLM, question: str, plan: dict | None = None) -> SqlAnswer:
    """`plan` lets the caller pass an already-generated routing/SQL response."""
    s = get_settings()
    feedback: list[dict] = []
    result = SqlAnswer(route="sql")
    for attempt in range(1, 3):
        result.attempts = attempt
        try:
            plan = plan if (plan and attempt == 1) else generate_sql(llm, question, feedback)
        except LLMError as exc:
            result.status, result.error = "ERROR", f"model error: {exc}"
            return result
        route = (plan.get("route") or "sql").lower()
        if route != "sql":
            result.route = route
            result.status = "ROUTED"
            return result
        raw_sql = plan.get("sql") or ""
        result.title = plan.get("title") or None
        try:
            safe_sql = sql_guard.validate(raw_sql, s.ai_row_limit)
            result.sql = safe_sql
            columns, rows, truncated = execute_readonly(safe_sql)
            result.columns, result.rows, result.truncated, result.row_count = columns, rows, truncated, len(rows)
            result.error = None
            break
        except sql_guard.UnsafeSQL as exc:
            result.sql = raw_sql
            result.status, result.error = "REJECTED", str(exc)
            problem = f"The query was rejected by the safety validator: {exc}"
        except psycopg.Error as exc:
            result.sql = raw_sql
            result.status, result.error = "ERROR", (exc.diag.message_primary if exc.diag else str(exc))
            problem = f"PostgreSQL returned an error: {result.error}"
        feedback = [{"role": "assistant", "content": json.dumps({"route": "sql", "sql": raw_sql})},
                    {"role": "user", "content": f"{problem}\nFix the query. Reply with the same JSON format."}]
    else:
        return result   # both attempts failed; status/error describe why

    result.status = "OK"
    result.chart = suggest_chart(result.columns, result.rows, plan.get("chart"), result.title)
    try:
        result.answer = llm.chat([{"role": "user", "content": SUMMARY_PROMPT.format(
            question=question, row_count=result.row_count,
            truncated=" (truncated)" if result.truncated else "",
            csv=_to_csv(result.columns, result.rows))}], temperature=0.2).strip()
    except LLMError:
        result.answer = f"Found {result.row_count} row(s). (The summary could not be generated.)"
    return result
