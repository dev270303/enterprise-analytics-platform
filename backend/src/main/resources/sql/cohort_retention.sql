-- Monthly cohort retention: % of each first-purchase cohort that ordered again N months later.
WITH valid AS (
    SELECT o.customer_id, date_trunc('month', o.order_date)::date AS month
    FROM orders o
    JOIN customers cu ON cu.id = o.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    WHERE o.status IN ('DELIVERED','SHIPPED')
      AND (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
),
cohorts AS (
    SELECT customer_id, min(month) AS cohort_month FROM valid GROUP BY customer_id
),
activity AS (
    SELECT DISTINCT c.cohort_month,
           (EXTRACT(YEAR FROM age(v.month, c.cohort_month)) * 12
            + EXTRACT(MONTH FROM age(v.month, c.cohort_month)))::int AS month_offset,
           v.customer_id
    FROM valid v JOIN cohorts c USING (customer_id)
),
sizes AS (
    SELECT cohort_month, count(*) AS cohort_size FROM cohorts GROUP BY cohort_month
)
SELECT
    a.cohort_month,
    s.cohort_size,
    a.month_offset,
    count(*)                                          AS active_customers,
    round(100.0 * count(*) / s.cohort_size, 1)        AS retention_pct
FROM activity a
JOIN sizes s USING (cohort_month)
WHERE a.cohort_month >= (SELECT max(cohort_month) FROM cohorts) - INTERVAL '11 months'
  AND a.month_offset BETWEEN 0 AND 11
GROUP BY a.cohort_month, s.cohort_size, a.month_offset
ORDER BY a.cohort_month, a.month_offset;
