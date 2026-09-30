-- Monthly revenue with 3-month moving average, MoM and YoY growth (window functions).
WITH monthly AS (
    SELECT date_trunc('month', o.order_date)::date AS month,
           sum(o.total_amount) AS revenue,
           count(*)            AS orders
    FROM orders o
    JOIN customers cu ON cu.id = o.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    WHERE o.status IN ('DELIVERED','SHIPPED')
      AND (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
    GROUP BY 1
)
SELECT
    month,
    revenue,
    orders,
    round(avg(revenue) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS moving_avg_3m,
    round(100 * (revenue / NULLIF(lag(revenue, 1)  OVER (ORDER BY month), 0) - 1), 1)       AS mom_growth_pct,
    round(100 * (revenue / NULLIF(lag(revenue, 12) OVER (ORDER BY month), 0) - 1), 1)       AS yoy_growth_pct
FROM monthly
ORDER BY month;
