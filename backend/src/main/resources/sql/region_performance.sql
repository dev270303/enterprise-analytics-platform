-- Revenue by region: share of total, YoY growth and best-performing city (ROW_NUMBER).
WITH anchor AS (SELECT max(order_date) AS end_date FROM orders),
city_rev AS (
    SELECT r.name AS region, ci.name AS city,
           sum(o.total_amount) FILTER (WHERE o.order_date >  a.end_date - INTERVAL '12 months') AS rev_current,
           sum(o.total_amount) FILTER (WHERE o.order_date <= a.end_date - INTERVAL '12 months') AS rev_previous
    FROM orders o
    JOIN customers cu ON cu.id = o.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    CROSS JOIN anchor a
    WHERE o.status IN ('DELIVERED','SHIPPED')
      AND o.order_date > a.end_date - INTERVAL '24 months'
    GROUP BY r.name, ci.name
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY region ORDER BY rev_current DESC) AS city_rank
    FROM city_rev
)
SELECT
    region,
    sum(rev_current)                                                        AS revenue,
    round(100 * sum(rev_current) / sum(sum(rev_current)) OVER (), 1)       AS share_pct,
    round(100 * (sum(rev_current) / NULLIF(sum(rev_previous), 0) - 1), 1)  AS yoy_growth_pct,
    max(city) FILTER (WHERE city_rank = 1)                                  AS top_city,
    count(*)                                                                AS cities
FROM ranked
GROUP BY region
ORDER BY revenue DESC;
