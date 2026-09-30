-- Category contribution: revenue, share, gross margin and units (trailing 12 months).
WITH anchor AS (SELECT max(order_date) AS end_date FROM orders)
SELECT
    p.category,
    round(sum(oi.quantity * oi.unit_price * (1 - oi.discount)), 2)                    AS revenue,
    round(100 * sum(oi.quantity * oi.unit_price * (1 - oi.discount))
              / sum(sum(oi.quantity * oi.unit_price * (1 - oi.discount))) OVER (), 1)  AS share_pct,
    round(100 * (1 - sum(oi.quantity * p.unit_cost)
                   / NULLIF(sum(oi.quantity * oi.unit_price * (1 - oi.discount)), 0)), 1) AS margin_pct,
    sum(oi.quantity)                                                                   AS units
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
GROUP BY p.category
ORDER BY revenue DESC;
