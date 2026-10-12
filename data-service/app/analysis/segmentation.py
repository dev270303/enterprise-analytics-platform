"""Customer segmentation with RFM features + K-Means, and a churn-risk indicator.

Features (per customer, revenue orders only, as of the last order date in the data)
  Recency   days since last order
  Frequency number of orders
  Monetary  total spend (INR)

Steps
  1. log1p-transform (all three are heavily right-skewed), then standardise.
  2. K-Means with k=4 by default: four segments are easy to act on. Silhouette scores for
     k=3..6 are still computed and stored so the choice is transparent.
  3. Name clusters from their centroids in standardised space:
       recent  = centroid recency below average      value = frequency+monetary above average
       recent & high value -> Champions      recent & low value -> Promising
       lapsed & high value -> At Risk        lapsed & low value -> Hibernating
     (a second cluster in the same quadrant gets Loyal / New Customers / Cooling Off / Lost)
  4. Churn risk: compares a customer's recency with their own typical gap between orders.
     HIGH   recency > 3x their average gap (or > 180 days for one-time buyers)
     MEDIUM recency > 1.5x their average gap (or > 90 days)
     LOW    otherwise
  5. Write customer_segments (full refresh) + a segmentation_runs summary row.
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from app.db import connect

FEATURE_SQL = """
WITH o AS (
    SELECT customer_id, order_date, total_amount
    FROM orders WHERE status IN ('DELIVERED','SHIPPED')
),
asof AS (SELECT max(order_date) AS d FROM o),
per_customer AS (
    SELECT customer_id,
           (SELECT d FROM asof) - max(order_date)          AS recency_days,
           count(*)                                        AS frequency,
           sum(total_amount)                               AS monetary,
           CASE WHEN count(*) > 1
                THEN (max(order_date) - min(order_date))::numeric / (count(*) - 1) END AS avg_gap_days
    FROM o GROUP BY customer_id
)
SELECT pc.*, (SELECT d FROM asof) AS as_of FROM per_customer pc
"""

QUADRANT_NAMES = {
    (True, True): ["Champions", "Loyal"],
    (True, False): ["Promising", "New Customers"],
    (False, True): ["At Risk", "Cooling Off"],
    (False, False): ["Hibernating", "Lost"],
}
DEFAULT_K = 4


def load_features() -> pd.DataFrame:
    with connect() as conn:
        rows = conn.execute(FEATURE_SQL).fetchall()
    df = pd.DataFrame(rows)
    for col in ("recency_days", "frequency"):
        df[col] = df[col].astype(int)
    df["monetary"] = df["monetary"].astype(float)
    df["avg_gap_days"] = pd.to_numeric(df["avg_gap_days"], errors="coerce").astype(float)
    return df


def churn_risk(recency: float, avg_gap: float | None) -> str:
    if avg_gap is None or np.isnan(avg_gap) or avg_gap <= 0:
        return "HIGH" if recency > 180 else "MEDIUM" if recency > 90 else "LOW"
    ratio = recency / avg_gap
    return "HIGH" if ratio > 3 else "MEDIUM" if ratio > 1.5 else "LOW"


def name_clusters(centroids: np.ndarray) -> dict[int, str]:
    """centroids in standardised (recency, frequency, monetary) space -> business label."""
    groups: dict[tuple[bool, bool], list[tuple[float, int]]] = {}
    for idx, (rec, freq, mon) in enumerate(centroids):
        value = (freq + mon) / 2
        groups.setdefault((rec < 0, value > 0), []).append((value, idx))
    labels = {}
    for quadrant, members in groups.items():
        names = QUADRANT_NAMES[quadrant]
        for rank, (_, idx) in enumerate(sorted(members, reverse=True)):
            labels[idx] = names[rank] if rank < len(names) else f"{names[-1]} {rank}"
    return labels


def segment(df: pd.DataFrame, k: int | None = DEFAULT_K, random_state: int = 42) -> tuple[pd.DataFrame, dict]:
    features = np.column_stack([
        np.log1p(df["recency_days"].to_numpy(float)),
        np.log1p(df["frequency"].to_numpy(float)),
        np.log1p(df["monetary"].to_numpy(float)),
    ])
    X = StandardScaler().fit_transform(features)

    scores = {}
    rng = np.random.default_rng(random_state)
    sample = rng.choice(len(X), size=min(3000, len(X)), replace=False)
    candidates = sorted({3, 4, 5, 6} | ({k} if k else set()))
    models = {}
    for kk in candidates:
        model = KMeans(n_clusters=kk, n_init=10, random_state=random_state).fit(X)
        models[kk] = model
        scores[kk] = float(silhouette_score(X[sample], model.labels_[sample])) if kk > 1 else 0.0
    best_k = k or max(scores, key=scores.get)
    model = models[best_k]

    centroids = model.cluster_centers_
    label_for = name_clusters(centroids)
    rfm_score = -centroids[:, 0] + centroids[:, 1] + centroids[:, 2]
    order = np.argsort(-rfm_score)          # best clusters first in the summary

    out = df.copy()
    out["cluster"] = model.labels_.astype(int)
    out["segment_label"] = out["cluster"].map(label_for)
    out["churn_risk"] = [churn_risk(r, g) for r, g in zip(out["recency_days"], out["avg_gap_days"])]

    summary = {
        "k": int(best_k),
        "silhouette": round(scores[best_k], 4),
        "silhouette_by_k": {str(kk): round(v, 4) for kk, v in scores.items()},
        "clusters": [
            {
                "cluster": int(c),
                "label": label_for[int(c)],
                "customers": int((out["cluster"] == c).sum()),
                "median_recency_days": float(out.loc[out["cluster"] == c, "recency_days"].median()),
                "median_frequency": float(out.loc[out["cluster"] == c, "frequency"].median()),
                "median_monetary": round(float(out.loc[out["cluster"] == c, "monetary"].median()), 2),
                "high_churn_risk_pct": round(100 * float((out.loc[out["cluster"] == c, "churn_risk"] == "HIGH").mean()), 1),
            }
            for c in order
        ],
    }
    return out, summary


def run(k: int | None = DEFAULT_K) -> dict:
    df = load_features()
    if len(df) < 50:
        raise ValueError("not enough customers with orders to segment")
    result, summary = segment(df, k)
    as_of: date = df["as_of"].iloc[0]
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM customer_segments")
        with cur.copy("COPY customer_segments (customer_id, recency_days, frequency, monetary, avg_gap_days, "
                      "cluster, segment_label, churn_risk) FROM STDIN") as copy:
            for r in result.itertuples(index=False):
                copy.write_row((int(r.customer_id), int(r.recency_days), int(r.frequency), round(float(r.monetary), 2),
                                None if np.isnan(r.avg_gap_days) else round(float(r.avg_gap_days), 1),
                                int(r.cluster), r.segment_label, r.churn_risk))
        cur.execute("INSERT INTO segmentation_runs (k, silhouette, customers, as_of_date, summary) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (summary["k"], summary["silhouette"], len(result), as_of, json.dumps(summary)))
        summary["run_id"] = cur.fetchone()["id"]
        conn.commit()
    summary["customers"] = int(len(result))
    summary["as_of"] = str(as_of)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
