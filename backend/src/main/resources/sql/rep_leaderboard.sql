-- Top 3 sales reps per region (trailing 12 months) using ROW_NUMBER() inside each region.
WITH anchor AS (SELECT max(order_date) AS end_date FROM orders),
rep_sales AS (
    SELECT r.name AS region, e.id, e.full_name,
           count(*)            AS orders,
           sum(o.total_amount) AS revenue
    FROM orders o
    JOIN employees e ON e.id = o.sales_rep_id
    JOIN cities ci   ON ci.id = e.city_id
    JOIN regions r   ON r.id = ci.region_id
    CROSS JOIN anchor a
    WHERE o.status IN ('DELIVERED','SHIPPED')
      AND o.order_date > a.end_date - INTERVAL '12 months'
    GROUP BY r.name, e.id, e.full_name
),
ranked AS (
    SELECT rs.*,
           row_number() OVER (PARTITION BY region ORDER BY revenue DESC)       AS region_rank,
           round(100 * revenue / sum(revenue) OVER (PARTITION BY region), 1)  AS region_share_pct
    FROM rep_sales rs
)
SELECT region, region_rank, full_name, orders, round(revenue, 2) AS revenue, region_share_pct
FROM ranked
WHERE region_rank <= 3
ORDER BY region, region_rank;
