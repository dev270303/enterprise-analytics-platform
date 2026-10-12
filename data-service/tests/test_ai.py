import psycopg
import pytest

from app.ai import assistant, rag, sql_agent, sql_guard
from app.ai.llm import extract_json, fit_dimension, set_llm_for_tests
from tests.fakes import FakeLLM

REGION_SQL = ("SELECT region, round(sum(total_amount)) AS revenue FROM ai.orders WHERE is_revenue "
              "GROUP BY region ORDER BY revenue DESC")


@pytest.fixture(autouse=True)
def _reset_llm():
    yield
    set_llm_for_tests(None)


# ------------------------------------------------------------------ guard

@pytest.mark.parametrize("sql", [
    REGION_SQL,
    "with m as (select date_trunc('month', order_date) m, sum(total_amount) r from orders group by 1) select * from m",
    "select product_name, rank() over (order by sum(line_revenue) desc) from ai.order_lines group by 1",
    "```sql\nSELECT count(*) FROM ai.tickets WHERE sla_breached;\n```",
])
def test_guard_accepts_reads(sql):
    out = sql_guard.validate(sql, row_limit=50)
    assert "LIMIT 50" in out


@pytest.mark.parametrize("sql", [
    "DROP TABLE orders", "select * from app_users", "select * from public.orders", "delete from ai.orders",
    "select 1; drop table orders", "select pg_sleep(10)", "select * into t from ai.orders",
    "select * from ai.orders for update", "copy ai.orders to '/tmp/x'", "select set_config('role','x',false)",
    "select * from generate_series(1,5)", "SET ROLE postgres", "select * from ai.not_a_view",
])
def test_guard_rejects(sql):
    with pytest.raises(sql_guard.UnsafeSQL):
        sql_guard.validate(sql)


def test_guard_caps_large_limits():
    assert "LIMIT 200" in sql_guard.validate("select * from ai.orders limit 100000", row_limit=200)


# ------------------------------------------------------------------ database layers (even if the guard were bypassed)

def test_readonly_role_blocks_private_tables():
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sql_agent.execute_readonly("SELECT * FROM public.app_users")
    assert sql_agent.role_isolation_available() is True


def test_readonly_transaction_blocks_writes():
    with pytest.raises(psycopg.Error):
        sql_agent.execute_readonly("DELETE FROM ai.orders")


def test_row_cap():
    cols, rows, truncated = sql_agent.execute_readonly("SELECT order_id FROM ai.orders")
    assert truncated and len(rows) == 200 and cols == ["order_id"]


# ------------------------------------------------------------------ agent

def test_agent_answers_with_rows_and_chart():
    fake = FakeLLM({"revenue by region": {"route": "sql", "sql": REGION_SQL, "chart": "bar", "title": "Revenue"}})
    result = sql_agent.answer(fake, "Revenue by region")
    assert result.status == "OK" and result.row_count == 4
    assert result.columns == ["region", "revenue"]
    assert result.chart["type"] == "bar"
    assert result.answer.startswith("Summary")


def test_agent_self_corrects_after_db_error():
    bad = {"route": "sql", "sql": "SELECT regionn FROM ai.orders"}
    good = {"route": "sql", "sql": REGION_SQL}
    fake = FakeLLM({"revenue by region": [bad, good]})
    result = sql_agent.answer(fake, "Revenue by region please")
    assert result.status == "OK" and result.attempts == 2
    assert "PostgreSQL returned an error" in fake.calls[1][-1]["content"]


def test_agent_refuses_unsafe_sql_twice():
    fake = FakeLLM({"delete": {"route": "sql", "sql": "DELETE FROM ai.orders"}})
    result = sql_agent.answer(fake, "delete everything")
    assert result.status == "REJECTED" and result.rows == []


def test_chart_suggestions():
    assert sql_agent.suggest_chart(["month", "revenue"], [["2026-01-01", 1.0], ["2026-02-01", 2.0], ["2026-03-01", 3.0]], None, "t")["type"] == "line"
    assert sql_agent.suggest_chart(["total"], [[42]], None, "t")["type"] == "number"
    assert sql_agent.suggest_chart(["a", "b"], [["x", "y"]], None, "t")["type"] == "table"


def test_extract_json_and_dimensions():
    assert extract_json('Sure! ```json\n{"route": "sql", "sql": "select \\"x\\""}\n```')["route"] == "sql"
    assert extract_json('noise {"a": {"b": 1}} trailing')["a"]["b"] == 1
    v = fit_dimension([3.0, 4.0, 1.0], 2)
    assert v == pytest.approx([0.6, 0.8])


# ------------------------------------------------------------------ RAG

def test_rag_hybrid_retrieval_and_citations():
    fake = FakeLLM()
    set_llm_for_tests(fake)
    results = rag.ingest_knowledge_base(uploaded_by="test")
    assert {r["status"] for r in results} <= {"created", "updated", "unchanged"}
    passages = rag.retrieve("What is the refund policy for orders above 50,000?", llm=fake)
    assert passages and passages[0].source == "refund_and_returns_policy.md"
    assert "semantic" in passages[0].matched_by or "keyword" in passages[0].matched_by
    out = rag.answer("What is the refund policy for orders above 50,000?", llm=fake)
    assert out["mode"] == "generative" and out["sources"][0]["cited"]


def test_rag_extractive_without_model():
    out = rag.answer("How many hours is the SLA for CRITICAL tickets?", llm=None)
    assert out["mode"] == "extractive"
    assert any(s["source"] == "support_sla_policy.md" for s in out["sources"])


def test_assistant_routes_and_logs():
    fake = FakeLLM({"revenue by region": {"route": "sql", "sql": REGION_SQL}})
    set_llm_for_tests(fake)
    before = assistant.questions_today()
    sql_reply = assistant.ask("Revenue by region", "tester")
    doc_reply = assistant.ask("What is the warranty policy for monitors?", "tester")
    other = assistant.ask("Tell me a joke", "tester")
    assert sql_reply["route"] == "sql" and sql_reply["status"] == "OK"
    assert doc_reply["route"] == "docs"
    assert other["route"] == "unsupported"
    assert assistant.questions_today() == before + 3


def test_assistant_without_model_still_answers_policy_questions():
    set_llm_for_tests(None)
    reply = assistant.ask("What is the policy on discounts above 35%?", "tester")
    assert reply["route"] == "docs" and reply["mode"] == "extractive"
    data_reply = assistant.ask("Revenue by region", "tester")
    assert data_reply["status"] == "NO_MODEL"
