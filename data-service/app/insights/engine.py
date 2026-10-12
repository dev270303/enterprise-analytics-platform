"""Automated daily insights: KPIs, anomaly detection and SLA monitoring.

Windows are anchored on the latest date in the data (as_of), so the report is meaningful
for historical data too:

  current week  = as_of-6 .. as_of            previous week = the 7 days before that
  baseline      = the 8 weeks before the current week

Anomaly rule (per region / city / category / channel / product):
  z = (current - mean(baseline)) / std(baseline)
  flagged when |z| >= 2.0 AND the change vs the baseline mean is at least 15%
  AND the baseline is material (ignores tiny series where % changes are noise).
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

from app.db import connect

Z_THRESHOLD = 2.0
MIN_PCT_CHANGE = 15.0
MIN_BASELINE_REVENUE = 2_00_000      # ₹2 L per week

WEEKLY_SQL = """
WITH a AS (SELECT %(as_of)s::date AS d),
lines AS (
    SELECT ((SELECT d FROM a) - o.order_date) / 7 AS weeks_ago,
           r.name AS region, ci.name AS city, p.category, o.channel, p.name AS product,
           oi.quantity * oi.unit_price * (1 - oi.discount) AS revenue, o.id AS order_id
    FROM order_items oi
    JOIN orders o     ON o.id = oi.order_id
    JOIN products p   ON p.id = oi.product_id
    JOIN customers cu ON cu.id = o.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    WHERE o.status IN ('DELIVERED','SHIPPED')
      AND o.order_date BETWEEN (SELECT d FROM a) - 62 AND (SELECT d FROM a)
)
SELECT '{dim}' AS dimension, {dim} AS member, weeks_ago,
       sum(revenue)::float AS revenue, count(DISTINCT order_id) AS orders
FROM lines WHERE weeks_ago BETWEEN 0 AND 8
GROUP BY {dim}, weeks_ago
"""


def _pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return round(100.0 * (cur / prev - 1), 1)


def _inr(v: float) -> str:
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:.1f} L"
    return f"₹{v:,.0f}"


def compute(as_of: date | None = None) -> dict:
    with connect() as conn:
        if as_of is None:
            as_of = conn.execute("SELECT max(order_date) AS d FROM orders").fetchone()["d"]
        p = {"as_of": as_of}

        k = conn.execute("""
            WITH a AS (SELECT %(as_of)s::date AS d),
            o AS (SELECT o.*, ((SELECT d FROM a) - o.order_date) / 7 AS w FROM orders o
                  WHERE o.order_date BETWEEN (SELECT d FROM a) - 13 AND (SELECT d FROM a))
            SELECT
              sum(total_amount) FILTER (WHERE w = 0 AND status IN ('DELIVERED','SHIPPED'))::float AS rev_cur,
              sum(total_amount) FILTER (WHERE w = 1 AND status IN ('DELIVERED','SHIPPED'))::float AS rev_prev,
              count(*) FILTER (WHERE w = 0 AND status IN ('DELIVERED','SHIPPED')) AS orders_cur,
              count(*) FILTER (WHERE w = 1 AND status IN ('DELIVERED','SHIPPED')) AS orders_prev,
              count(*) FILTER (WHERE w = 0 AND status = 'RETURNED') AS returns_cur,
              count(*) FILTER (WHERE w = 1 AND status = 'RETURNED') AS returns_prev,
              sum(total_amount) FILTER (WHERE order_date = (SELECT d FROM a) AND status IN ('DELIVERED','SHIPPED'))::float AS rev_day
            FROM o""", p).fetchone()

        nc = conn.execute("""
            WITH f AS (SELECT customer_id, min(order_date) AS first_order FROM orders
                       WHERE status IN ('DELIVERED','SHIPPED') GROUP BY customer_id)
            SELECT count(*) FILTER (WHERE first_order > %(as_of)s::date - 7) AS cur,
                   count(*) FILTER (WHERE first_order BETWEEN %(as_of)s::date - 13 AND %(as_of)s::date - 7) AS prev
            FROM f""", p).fetchone()

        t = conn.execute("""
            WITH a AS (SELECT (SELECT max(opened_at) FROM tickets) AS ts, %(as_of)s::date + 1 AS d_end)
            SELECT
              count(*) FILTER (WHERE opened_at >= (SELECT d_end FROM a) - 7)                                   AS opened_cur,
              count(*) FILTER (WHERE opened_at >= (SELECT d_end FROM a) - 14 AND opened_at < (SELECT d_end FROM a) - 7) AS opened_prev,
              count(*) FILTER (WHERE resolved_at >= (SELECT d_end FROM a) - 7 AND resolved_at < (SELECT d_end FROM a)
                                 AND resolved_at > opened_at + make_interval(hours => sla_hours))             AS breached_cur,
              count(*) FILTER (WHERE resolved_at >= (SELECT d_end FROM a) - 7 AND resolved_at < (SELECT d_end FROM a)) AS resolved_cur,
              count(*) FILTER (WHERE resolved_at >= (SELECT d_end FROM a) - 14 AND resolved_at < (SELECT d_end FROM a) - 7
                                 AND resolved_at > opened_at + make_interval(hours => sla_hours))             AS breached_prev,
              count(*) FILTER (WHERE resolved_at >= (SELECT d_end FROM a) - 14 AND resolved_at < (SELECT d_end FROM a) - 7) AS resolved_prev,
              count(*) FILTER (WHERE resolved_at IS NULL AND opened_at < (SELECT d_end FROM a)
                                 AND (SELECT d_end FROM a) > opened_at + make_interval(hours => sla_hours))   AS open_overdue,
              count(*) FILTER (WHERE resolved_at IS NULL AND opened_at < (SELECT d_end FROM a)
                                 AND priority IN ('HIGH','CRITICAL'))                                          AS open_high
            FROM tickets""", p).fetchone()

        sla_by_cat = conn.execute("""
            SELECT category, count(*) AS resolved,
                   count(*) FILTER (WHERE resolved_at > opened_at + make_interval(hours => sla_hours)) AS breached
            FROM tickets WHERE resolved_at >= %(as_of)s::date - 6 AND resolved_at < %(as_of)s::date + 1
            GROUP BY category ORDER BY breached DESC""", p).fetchall()

        series: list[dict] = []
        for dim in ("region", "city", "category", "channel", "product"):
            series += conn.execute(WEEKLY_SQL.replace("{dim}", dim), p).fetchall()

    anomalies = detect_anomalies(series)
    kpis = [
        _kpi("Revenue (7 days)", k["rev_cur"], k["rev_prev"], money=True),
        _kpi("Orders (7 days)", k["orders_cur"], k["orders_prev"]),
        _kpi("Avg order value", (k["rev_cur"] or 0) / k["orders_cur"] if k["orders_cur"] else None,
             (k["rev_prev"] or 0) / k["orders_prev"] if k["orders_prev"] else None, money=True),
        _kpi("New customers", nc["cur"], nc["prev"]),
        _kpi("Returns", k["returns_cur"], k["returns_prev"], higher_is_better=False),
        _kpi("Tickets opened", t["opened_cur"], t["opened_prev"], higher_is_better=False),
    ]
    breach_rate = 100.0 * t["breached_cur"] / t["resolved_cur"] if t["resolved_cur"] else None
    breach_rate_prev = 100.0 * t["breached_prev"] / t["resolved_prev"] if t["resolved_prev"] else None

    bullets = []
    rev = kpis[0]
    if rev["change_pct"] is not None:
        bullets.append(f"Revenue {'increased' if rev['change_pct'] >= 0 else 'decreased'} {abs(rev['change_pct']):.1f}% "
                       f"compared with the previous week, to {_inr(rev['value'] or 0)}.")
    orders = kpis[1]
    if orders["change_pct"] is not None:
        bullets.append(f"{orders['value']:,} orders were placed, {orders['change_pct']:+.1f}% week over week.")
    shown_products = 0
    for a in anomalies:
        if len([b for b in bullets if "versus its 8-week average" in b]) >= 4:
            break
        if a["dimension"] == "product":
            if shown_products >= 2:
                continue
            shown_products += 1
        verb = "jumped" if a["change_pct"] > 0 else "fell"
        bullets.append(f"{a['member']} ({a['dimension']}) revenue {verb} {abs(a['change_pct']):.0f}% versus its 8-week average "
                       f"({_inr(a['current'])} vs {_inr(a['baseline_mean'])}, z = {a['z']:+.1f}).")
    bullets.append(f"{t['breached_cur']} support tickets breached their SLA in the last 7 days"
                   + (f" ({breach_rate:.1f}% of resolved tickets, previous week {breach_rate_prev:.1f}%)." if breach_rate is not None and breach_rate_prev is not None else "."))
    if t["open_overdue"]:
        bullets.append(f"{t['open_overdue']} open tickets are already past their SLA and need escalation.")

    headline = bullets[0] if bullets else f"Daily business report for {as_of:%d %b %Y}"
    return {
        "as_of": as_of.isoformat(),
        "period": {"current_start": (as_of - timedelta(days=6)).isoformat(), "current_end": as_of.isoformat()},
        "headline": headline,
        "bullets": bullets,
        "kpis": kpis,
        "yesterday_revenue": k["rev_day"],
        "anomalies": anomalies[:12],
        "sla": {"breached_7d": t["breached_cur"], "resolved_7d": t["resolved_cur"],
                "breach_rate_pct": round(breach_rate, 1) if breach_rate is not None else None,
                "breach_rate_prev_pct": round(breach_rate_prev, 1) if breach_rate_prev is not None else None,
                "open_overdue": t["open_overdue"], "open_high_priority": t["open_high"],
                "by_category": [dict(r) for r in sla_by_cat]},
    }


def _kpi(label, cur, prev, money=False, higher_is_better=True):
    cur = float(cur) if cur is not None else None
    prev = float(prev) if prev is not None else None
    change = _pct(cur, prev)
    good = None if change is None else ((change >= 0) == higher_is_better)
    return {"label": label, "value": round(cur, 2) if money and cur is not None else (int(cur) if cur is not None else None),
            "previous": prev, "change_pct": change, "money": money, "good": good}


def detect_anomalies(series: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], dict[int, float]] = {}
    for r in series:
        groups.setdefault((r["dimension"], r["member"]), {})[int(r["weeks_ago"])] = float(r["revenue"] or 0)
    found = []
    for (dim, member), weeks in groups.items():
        baseline = [weeks.get(w, 0.0) for w in range(1, 9)]
        current = weeks.get(0, 0.0)
        mean = sum(baseline) / len(baseline)
        if mean < MIN_BASELINE_REVENUE:
            continue
        std = math.sqrt(sum((x - mean) ** 2 for x in baseline) / (len(baseline) - 1))
        if std == 0:
            continue
        z = (current - mean) / std
        change = 100.0 * (current / mean - 1)
        if abs(z) >= Z_THRESHOLD and abs(change) >= MIN_PCT_CHANGE:
            found.append({"dimension": dim, "member": member, "current": round(current, 2),
                          "baseline_mean": round(mean, 2), "baseline_std": round(std, 2),
                          "z": round(z, 2), "change_pct": round(change, 1),
                          "direction": "up" if z > 0 else "down"})
    priority = {"region": 0, "category": 1, "city": 2, "channel": 3, "product": 4}
    # business-level segments first (region, category, city, channel), then individual products
    return sorted(found, key=lambda a: (priority.get(a["dimension"], 9), -abs(a["z"])))


def to_json(payload: dict) -> str:
    return json.dumps(payload, default=str)
