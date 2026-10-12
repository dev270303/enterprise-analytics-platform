"""Single entry point for the AI analyst: routes a question to SQL or documents,
enforces the global daily budget, and writes every question to ai_queries."""
from __future__ import annotations

import re
import time
from dataclasses import asdict

from app.ai import rag, sql_agent
from app.ai.llm import LLMError, get_llm
from app.config import get_settings
from app.db import connect, fetch_one

# Cheap pre-router: obvious policy questions skip the SQL-generation call entirely.
DOC_HINTS = re.compile(r"\b(polic(y|ies)|warrant(y|ies)|guideline|procedure|eligib|entitle|approv|allowed to|"
                       r"credit terms|restocking|escalat|what is the (refund|return|sla)|how (long|many days) (do|does|can|will))\b",
                       re.I)
MAX_QUESTION_CHARS = 500


class BudgetExceeded(RuntimeError):
    pass


def questions_today() -> int:
    row = fetch_one("SELECT count(*) AS n FROM ai_queries WHERE created_at >= date_trunc('day', now())")
    return int(row["n"]) if row else 0


def _log(asked_by, question, route, status, sql=None, error=None, row_count=None, attempts=None, started=None):
    with connect(autocommit=True) as conn:
        conn.execute("""INSERT INTO ai_queries (asked_by, question, route, generated_sql, status, error_message,
                                                row_count, attempts, latency_ms)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                     (asked_by, question[:MAX_QUESTION_CHARS], route, sql, status, (error or "")[:1000] or None,
                      row_count, attempts, int((time.monotonic() - started) * 1000) if started else None))


def ask(question: str, asked_by: str | None = None) -> dict:
    started = time.monotonic()
    question = re.sub(r"\s+", " ", question or "").strip()
    if not question:
        raise ValueError("question is empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"question is longer than {MAX_QUESTION_CHARS} characters")
    limit = get_settings().ai_daily_limit
    if questions_today() >= limit:
        raise BudgetExceeded(f"The demo's daily AI budget ({limit} questions) has been used up. Please try again tomorrow.")

    llm = get_llm()
    if DOC_HINTS.search(question):
        return _docs(question, asked_by, started, llm)
    if llm is None:
        _log(asked_by, question, "sql", "NO_MODEL", started=started)
        return {"route": "sql", "status": "NO_MODEL",
                "answer": "Data questions need an AI model, and none is configured on this server (LLM_API_KEY). "
                          "Policy questions still work with keyword search."}

    try:
        plan = sql_agent.generate_sql(llm, question)
    except LLMError as exc:
        _log(asked_by, question, None, "ERROR", error=str(exc), started=started)
        return {"route": None, "status": "ERROR", "answer": f"The AI model could not be reached: {exc}"}

    route = (plan.get("route") or "sql").lower()
    if route == "docs":
        return _docs(question, asked_by, started, llm)
    if route == "unsupported":
        _log(asked_by, question, "unsupported", "UNSUPPORTED", started=started)
        return {"route": "unsupported", "status": "UNSUPPORTED",
                "answer": "I can answer questions about this company's sales, customers, products and support data, "
                          "or about its policies. Try e.g. \"Top 5 products by revenue in Delhi in the last 6 months\"."}

    result = sql_agent.answer(llm, question, plan)
    if result.status == "ROUTED" and result.route == "docs":
        return _docs(question, asked_by, started, llm)
    _log(asked_by, question, "sql", result.status, result.sql, result.error, result.row_count, result.attempts, started)
    payload = asdict(result)
    if result.status in ("REJECTED", "ERROR") and not payload["answer"]:
        payload["answer"] = ("I couldn't produce a safe, working query for that question. Try rephrasing it, "
                             "e.g. name the metric, the period and the breakdown you want.")
    payload["latency_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def _docs(question, asked_by, started, llm):
    result = rag.answer(question, llm)
    status = "OK" if result["sources"] else "NO_ANSWER"
    _log(asked_by, question, "docs", status, started=started)
    return {"route": "docs", "status": status, **result, "latency_ms": int((time.monotonic() - started) * 1000)}
