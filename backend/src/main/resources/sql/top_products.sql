-- Top 10 products by trailing-12-month revenue, with margin and rank inside their category.
WITH anchor AS (SELECT max(order_date) AS end_date FROM orders),
product_sales AS (
    SELECT p.id, p.sku, p.name, p.category,
           sum(oi.quantity)                                          AS units,
           sum(oi.quantity * oi.unit_price * (1 - oi.discount))      AS revenue,
           sum(oi.quantity * p.unit_cost)                            AS cost
    FROM order_items oi
    JOIN orders o     ON o.id = oi.order_id
    JOIN products p   ON p.id = oi.product_id
    JOIN customers cu ON cu.id = o.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    CROSS JOIN anchor a
    WHERE o.status IN ('DELIVERED','SHIPPED')
      AND o.order_date > a.end_date - INTERVAL '12 months'
      AND (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
    GROUP BY p.id, p.sku, p.name, p.category
)
SELECT
    rank() OVER (ORDER BY revenue DESC)                          AS overall_rank,
    rank() OVER (PARTITION BY category ORDER BY revenue DESC)    AS category_rank,
    sku, name, category, units,
    round(revenue, 2)                                            AS revenue,
    round(100 * (revenue - cost) / NULLIF(revenue, 0), 1)        AS margin_pct
FROM product_sales
ORDER BY revenue DESC
LIMIT 10;
