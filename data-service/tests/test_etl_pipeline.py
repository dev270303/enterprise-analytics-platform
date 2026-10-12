from pathlib import Path

import pytest

from app.db import fetch_one
from app.etl import generate
from app.etl.pipeline import run_batch, run_file


@pytest.fixture(scope="module")
def batch(tmp_path_factory) -> list[tuple[str, bytes, None]]:
    out = tmp_path_factory.mktemp("batch")
    generate.generate(out, seed=7, scale=0.3)
    return [(p.name, p.read_bytes(), None) for p in sorted(Path(out).glob("*.csv"))]


def _rules(result):
    return {(i.severity, i.rule) for i in result.issues}


def test_dry_run_reports_but_does_not_write(batch):
    before = fetch_one("SELECT count(*) AS n FROM orders")["n"]
    results = run_batch(batch, mode="DRY_RUN", source="test")
    assert fetch_one("SELECT count(*) AS n FROM orders")["n"] == before
    by_entity = {r.entity: r for r in results}
    assert set(by_entity) == {"customers", "orders", "order_items", "tickets"}
    # every injected problem class is caught
    assert ("ERROR", "invalid_email") in _rules(by_entity["customers"])
    assert ("ERROR", "unknown_city") in _rules(by_entity["customers"])
    assert ("ERROR", "future_date") in _rules(by_entity["customers"])
    assert ("INFO", "duplicate_row") in _rules(by_entity["customers"])
    assert ("ERROR", "invalid_date") in _rules(by_entity["orders"])
    assert ("ERROR", "unknown_reference") in _rules(by_entity["orders"])
    assert ("WARNING", "outlier") in _rules(by_entity["order_items"])
    assert ("ERROR", "out_of_range") in _rules(by_entity["order_items"])
    assert ("ERROR", "invalid_timeline") in _rules(by_entity["tickets"])
    # report survives the rollback
    run = fetch_one("SELECT * FROM etl_runs WHERE id = %s", (by_entity["orders"].run_id,))
    assert run["mode"] == "DRY_RUN" and run["rows_in"] == by_entity["orders"].rows_in
    assert fetch_one("SELECT count(*) AS n FROM etl_issues WHERE run_id = %s", (run["id"],))["n"] > 0


def test_commit_loads_and_is_idempotent(batch):
    first = {r.entity: r for r in run_batch(batch, mode="COMMIT", source="test")}
    assert first["orders"].rows_inserted > 0 and first["order_items"].rows_inserted > 0
    loaded = fetch_one("SELECT count(*) AS n, sum(total_amount) AS amt FROM orders WHERE etl_run_id = %s",
                       (first["orders"].run_id,))
    assert loaded["n"] == first["orders"].rows_loaded
    assert loaded["amt"] > 0                       # totals recomputed from the lines
    again = {r.entity: r for r in run_batch(batch, mode="COMMIT", source="test")}
    assert again["orders"].rows_inserted == 0
    assert again["orders"].rows_updated == first["orders"].rows_loaded


def test_missing_required_column_fails_file():
    result = run_file("orders.csv", b"order_id,order_date\nORD-X-0001,2026-10-01\n", mode="DRY_RUN", source="test")
    assert result.status == "FAILED"
    assert ("ERROR", "missing_column") in _rules(result)


def test_unrecognised_file():
    result = run_file("mystery.csv", b"a,b\n1,2\n", mode="DRY_RUN", source="test")
    assert result.status == "FAILED"
