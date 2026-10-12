-- Highest-value customers flagged HIGH churn risk: the account-management call list.
SELECT cu.full_name, cu.segment, ci.name AS city, cs.segment_label, cs.recency_days,
       cs.avg_gap_days, cs.frequency AS orders, round(cs.monetary, 2) AS lifetime_value
FROM customer_segments cs
JOIN customers cu ON cu.id = cs.customer_id
JOIN cities ci    ON ci.id = cu.city_id
JOIN regions r    ON r.id = ci.region_id
WHERE cs.churn_risk = 'HIGH'
  AND (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
ORDER BY cs.monetary DESC
LIMIT 10;
