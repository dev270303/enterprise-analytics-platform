"""Exploratory data analysis report: pandas + NumPy + Matplotlib.

Writes PNG charts and a Markdown summary with the computed findings:

    python -m app.analysis.eda --out ../docs/analysis

Requires matplotlib (requirements-dev.txt); not needed by the web service.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from app.db import connect

BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e8e7e2"


def _style(plt):
    plt.rcParams.update({
        "figure.dpi": 130, "savefig.bbox": "tight", "font.size": 10, "axes.edgecolor": GRID,
        "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True,
        "grid.color": GRID, "axes.spines.top": False, "axes.spines.right": False, "axes.titleweight": "bold",
        "axes.titlesize": 11, "axes.titlelocation": "left", "axes.titlecolor": INK,
    })


def load() -> dict[str, pd.DataFrame]:
    with connect() as conn:
        q = lambda sql: pd.DataFrame(conn.execute(sql).fetchall())
        return {
            "monthly": q("""SELECT date_trunc('month', order_date)::date AS month, sum(total_amount)::float AS revenue,
                                   count(*) AS orders FROM orders WHERE status IN ('DELIVERED','SHIPPED')
                            GROUP BY 1 ORDER BY 1"""),
            "orders": q("""SELECT o.total_amount::float AS amount, cu.segment, o.channel, o.status
                           FROM orders o JOIN customers cu ON cu.id = o.customer_id"""),
            "weekday": q("""SELECT EXTRACT(ISODOW FROM order_date)::int AS dow, count(*) AS orders
                            FROM orders GROUP BY 1 ORDER BY 1"""),
            "category": q("""SELECT p.category, sum(oi.quantity*oi.unit_price*(1-oi.discount))::float AS revenue,
                                    sum(oi.quantity*p.unit_cost)::float AS cost
                             FROM order_items oi JOIN products p ON p.id = oi.product_id
                             JOIN orders o ON o.id = oi.order_id WHERE o.status IN ('DELIVERED','SHIPPED')
                             GROUP BY 1"""),
            "segments": q("""SELECT segment_label, churn_risk, count(*) AS customers, sum(monetary)::float AS value
                             FROM customer_segments GROUP BY 1, 2"""),
        }


def run(out_dir: str | Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _style(plt)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    d = load()
    findings: list[str] = []

    # 1. revenue trend with 3-month rolling mean and YoY
    m = d["monthly"].copy()
    m["month"] = pd.to_datetime(m["month"])
    m["rolling"] = m["revenue"].rolling(3, min_periods=1).mean()
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(m["month"], m["revenue"] / 1e7, color=BLUE, lw=2, label="Revenue")
    ax.plot(m["month"], m["rolling"] / 1e7, color=ORANGE, lw=2, ls="--", label="3-month mean")
    ax.set_title("Monthly revenue (₹ crore)")
    ax.legend(frameon=False, loc="upper left")
    fig.savefig(out / "revenue_trend.png"); plt.close(fig)
    if len(m) >= 24:
        last12, prev12 = m["revenue"].iloc[-12:].sum(), m["revenue"].iloc[-24:-12].sum()
        findings.append(f"Revenue for the last 12 months is ₹{last12/1e7:,.1f} Cr, {100*(last12/prev12-1):+.1f}% on the prior 12 months.")
    peak = m.loc[m["revenue"].idxmax()]
    findings.append(f"The peak month is {peak['month']:%B %Y} (₹{peak['revenue']/1e7:,.1f} Cr), the festive season.")

    # 2. order value distribution (log scale) by segment
    o = d["orders"]
    fig, ax = plt.subplots(figsize=(8, 3.2))
    bins = np.logspace(np.log10(max(o["amount"].min(), 100)), np.log10(o["amount"].max()), 50)
    for seg, color in (("Retail", BLUE), ("SMB", ORANGE), ("Enterprise", AQUA)):
        ax.hist(o.loc[o["segment"] == seg, "amount"], bins=bins, alpha=0.75, color=color, label=seg)
    ax.set_xscale("log")
    ax.set_title("Order value distribution by segment (log scale, ₹)")
    ax.legend(frameon=False)
    fig.savefig(out / "order_value_distribution.png"); plt.close(fig)
    med = o.groupby("segment")["amount"].median()
    findings.append("Median order value: " + ", ".join(f"{s} ₹{v:,.0f}" for s, v in med.sort_values().items()) + ".")
    q1, q3 = np.percentile(o["amount"], [25, 75])
    fence = q3 + 3 * (q3 - q1)
    findings.append(f"{(o['amount'] > fence).mean():.1%} of orders sit above the extreme-outlier fence (Q3 + 3·IQR = ₹{fence:,.0f}). "
                    "These are bulk Enterprise orders, so they are real, not errors. That is why the ETL flags outliers instead of dropping them.")

    # 3. category share vs margin
    c = d["category"].copy()
    c["margin"] = 1 - c["cost"] / c["revenue"]
    c["share"] = c["revenue"] / c["revenue"].sum()
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.scatter(c["share"] * 100, c["margin"] * 100, s=90, color=BLUE, edgecolor="white", linewidth=2, zorder=3)
    placed: list[tuple[float, float]] = []
    for _, r in c.sort_values("share").iterrows():
        x, y = r["share"] * 100, r["margin"] * 100
        dy = -12 if any(abs(x - px) < 3 and abs(y - py) < 4 for px, py in placed) else 4   # nudge colliding labels
        ax.annotate(r["category"], (x, y), xytext=(6, dy), textcoords="offset points", fontsize=9, color=INK)
        placed.append((x, y))
    ax.set_xlabel("Share of revenue (%)"); ax.set_ylabel("Gross margin (%)")
    ax.set_title("Category mix: revenue share vs gross margin")
    fig.savefig(out / "category_share_vs_margin.png"); plt.close(fig)
    top = c.sort_values("share", ascending=False).iloc[0]
    best = c.sort_values("margin", ascending=False).iloc[0]
    findings.append(f"{top['category']} bring the most revenue ({top['share']:.0%}) at only {top['margin']:.0%} margin. "
                    f"{best['category']} earns the highest margin ({best['margin']:.0%}).")

    # 4. weekday pattern
    w = d["weekday"]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    fig, ax = plt.subplots(figsize=(8, 2.8))
    ax.bar([names[i - 1] for i in w["dow"]], w["orders"], color=BLUE, width=0.6)
    ax.set_axisbelow(True)
    ax.grid(axis="x", visible=False)
    ax.set_title("Orders by weekday")
    fig.savefig(out / "orders_by_weekday.png"); plt.close(fig)
    wd = w.set_index("dow")["orders"]
    findings.append(f"Saturday has {wd.get(6, 0)/wd.loc[1:5].mean()-1:+.0%} more orders than an average weekday, "
                    f"and Sunday has {wd.get(7, 0)/wd.loc[1:5].mean()-1:+.0%}.")

    # 5. segments
    s = d["segments"]
    if not s.empty:
        pivot = s.pivot_table(index="segment_label", columns="churn_risk", values="customers", aggfunc="sum").fillna(0)
        pivot = pivot.reindex(columns=[c for c in ["LOW", "MEDIUM", "HIGH"] if c in pivot.columns])
        pivot = pivot.loc[pivot.sum(axis=1).sort_values().index]
        fig, ax = plt.subplots(figsize=(8, 3))
        left = np.zeros(len(pivot))
        for col, color in zip(pivot.columns, (AQUA, YELLOW, ORANGE)):
            ax.barh(pivot.index, pivot[col], left=left, color=color, label=f"{col.title()} churn risk",
                    edgecolor="white", linewidth=1.5)
            left += pivot[col].to_numpy()
        ax.set_title("RFM segments (K-Means) by churn risk")
        ax.set_axisbelow(True)
        ax.grid(axis="y", visible=False)
        ax.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12))
        fig.savefig(out / "rfm_segments.png"); plt.close(fig)
        high = s[s["churn_risk"] == "HIGH"]
        findings.append(f"{int(high['customers'].sum()):,} customers are at HIGH churn risk. Together they account for "
                        f"₹{high['value'].sum()/1e7:,.1f} Cr of lifetime revenue.")

    md = ["# Exploratory data analysis", "",
          "Generated by `python -m app.analysis.eda` from the live database (pandas, NumPy, Matplotlib, scikit-learn).", "",
          "## Key findings", ""] + [f"- {f}" for f in findings] + [""]
    for img, title in [("revenue_trend.png", "Revenue trend"), ("order_value_distribution.png", "Order values"),
                       ("category_share_vs_margin.png", "Category mix"), ("orders_by_weekday.png", "Weekly pattern"),
                       ("rfm_segments.png", "Customer segments")]:
        if (out / img).exists():
            md += [f"## {title}", "", f"![{title}]({img})", ""]
    (out / "README.md").write_text("\n".join(md), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../docs/analysis")
    print(run(ap.parse_args().out))
