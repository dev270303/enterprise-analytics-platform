-- KPI cards: trailing 12 months vs the 12 months before, with growth %.
WITH anchor AS (
    SELECT max(order_date) AS end_date FROM orders
),
scoped AS (
    SELECT o.*,
           CASE WHEN o.order_date >  a.end_date - INTERVAL '12 months' THEN 'current'
                ELSE 'previous' END AS period
    FROM orders o
    JOIN customers cu ON cu.id = o.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    CROSS JOIN anchor a
    WHERE o.order_date > a.end_date - INTERVAL '24 months'
      AND (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
),
agg AS (
    SELECT period,
           sum(total_amount) FILTER (WHERE status IN ('DELIVERED','SHIPPED'))            AS revenue,
           count(*)          FILTER (WHERE status IN ('DELIVERED','SHIPPED'))            AS orders,
           count(DISTINCT customer_id) FILTER (WHERE status IN ('DELIVERED','SHIPPED'))  AS active_customers,
           count(*) FILTER (WHERE status = 'RETURNED')::numeric / NULLIF(count(*), 0)    AS return_rate
    FROM scoped
    GROUP BY period
)
SELECT
    c.revenue                                   AS revenue,
    round(100 * (c.revenue / NULLIF(p.revenue, 0) - 1), 1)                   AS revenue_growth_pct,
    c.orders                                    AS orders,
    round(100 * (c.orders::numeric / NULLIF(p.orders, 0) - 1), 1)           AS orders_growth_pct,
    c.active_customers                          AS active_customers,
    round(100 * (c.active_customers::numeric / NULLIF(p.active_customers, 0) - 1), 1) AS customers_growth_pct,
    round(c.revenue / NULLIF(c.orders, 0), 0)   AS avg_order_value,
    round(100 * ((c.revenue / NULLIF(c.orders, 0)) / NULLIF(p.revenue / NULLIF(p.orders, 0), 0) - 1), 1) AS aov_growth_pct,
    round(100 * c.return_rate, 2)               AS return_rate_pct,
    (SELECT end_date FROM anchor)               AS as_of
FROM agg c
LEFT JOIN agg p ON p.period = 'previous'
WHERE c.period = 'current';
