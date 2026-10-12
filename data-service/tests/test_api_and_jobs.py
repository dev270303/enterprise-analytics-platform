from fastapi.testclient import TestClient

from app.ai.llm import set_llm_for_tests
from app.analysis import segmentation
from app.db import fetch_one
from app.insights import engine, report
from app.main import app
from tests.fakes import FakeLLM

client = TestClient(app)
H = {"X-Internal-Token": "test-token"}


def test_health_is_public_and_status_is_protected():
    assert client.get("/health").json()["status"] == "UP"
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"X-Internal-Token": "wrong"}).status_code == 401
    body = client.get("/status", headers=H).json()
    assert body["database"] is True and body["role_isolation"] is True


def test_upload_dry_run_endpoint():
    csv = b"Email,Customer Name,Segment,City,Signup Date\napi.user@example.com,api user,retail,Bangalore,2026-01-05\n"
    r = client.post("/etl/upload", params={"file_name": "customers.csv", "commit": "false", "user": "t"},
                    content=csv, headers={**H, "Content-Type": "text/csv"})
    assert r.status_code == 200
    body = r.json()
    assert body["entity"] == "customers" and body["rows_loaded"] == 1 and body["mode"] == "DRY_RUN"
    assert fetch_one("SELECT count(*) AS n FROM customers WHERE email = 'api.user@example.com'")["n"] == 0


def test_ask_endpoint_with_fake_model():
    set_llm_for_tests(FakeLLM({"by region": {"route": "sql", "sql": "SELECT region, count(*) AS orders FROM ai.orders GROUP BY 1"}}))
    try:
        r = client.post("/ai/ask", json={"question": "Orders by region", "user": "t"}, headers=H)
        assert r.status_code == 200 and r.json()["row_count"] == 4
        assert client.post("/ai/ask", json={"question": ""}, headers=H).status_code == 422
    finally:
        set_llm_for_tests(None)


def test_segmentation_job():
    summary = segmentation.run()
    assert summary["k"] == 4 and summary["customers"] > 1000
    labels = {c["label"] for c in summary["clusters"]}
    assert labels <= {"Champions", "Loyal", "Promising", "New Customers", "At Risk", "Cooling Off", "Hibernating", "Lost"}
    assert fetch_one("SELECT count(*) AS n FROM customer_segments")["n"] == summary["customers"]


def test_anomaly_detection_rules():
    series = [{"dimension": "region", "member": "West", "weeks_ago": w, "revenue": 1_000_000 + (w % 2) * 50_000} for w in range(1, 9)]
    series.append({"dimension": "region", "member": "West", "weeks_ago": 0, "revenue": 2_000_000})
    series += [{"dimension": "city", "member": "Tiny", "weeks_ago": w, "revenue": 1000} for w in range(0, 9)]
    found = engine.detect_anomalies(series)
    assert len(found) == 1 and found[0]["member"] == "West" and found[0]["direction"] == "up"


def test_insights_report_is_stored():
    set_llm_for_tests(FakeLLM())
    try:
        result = report.generate(trigger="test", send_email=True)   # SMTP not configured -> skipped, not failed
    finally:
        set_llm_for_tests(None)
    assert result["bullets"] and result["summary"]
    assert result["email_status"].startswith("SKIPPED")
    row = fetch_one("SELECT html, payload FROM insight_reports WHERE id = %s", (result["id"],))
    assert "Daily Business Intelligence Report" in row["html"]
    assert row["payload"]["kpis"][0]["label"].startswith("Revenue")
