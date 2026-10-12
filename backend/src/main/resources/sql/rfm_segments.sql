-- Machine-learning (RFM + K-Means) customer segments with churn-risk mix.
-- Populated by the data service's segmentation job; empty until it has run once.
WITH scoped AS (
    SELECT cs.*
    FROM customer_segments cs
    JOIN customers cu ON cu.id = cs.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    WHERE (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
)
SELECT
    segment_label,
    count(*)                                                       AS customers,
    round(100.0 * count(*) / sum(count(*)) OVER (), 1)             AS customer_share_pct,
    round(sum(monetary), 2)                                        AS lifetime_value,
    round(100 * sum(monetary) / sum(sum(monetary)) OVER (), 1)     AS value_share_pct,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY recency_days)      AS median_recency_days,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY frequency)         AS median_orders,
    count(*) FILTER (WHERE churn_risk = 'HIGH')                    AS high_churn_risk,
    count(*) FILTER (WHERE churn_risk = 'MEDIUM')                  AS medium_churn_risk,
    (SELECT max(computed_at) FROM customer_segments)               AS computed_at
FROM scoped
GROUP BY segment_label
ORDER BY lifetime_value DESC;
