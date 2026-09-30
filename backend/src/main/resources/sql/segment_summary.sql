-- Customer segments: size, revenue share and average lifetime value.
WITH ltv AS (
    SELECT cu.id, cu.segment, coalesce(sum(o.total_amount), 0) AS value, count(o.id) AS orders
    FROM customers cu
    JOIN cities ci  ON ci.id = cu.city_id
    JOIN regions r  ON r.id = ci.region_id
    LEFT JOIN orders o ON o.customer_id = cu.id AND o.status IN ('DELIVERED','SHIPPED')
    WHERE (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
    GROUP BY cu.id, cu.segment
)
SELECT
    segment,
    count(*)                                                 AS customers,
    round(sum(value), 2)                                     AS revenue,
    round(100 * sum(value) / sum(sum(value)) OVER (), 1)     AS revenue_share_pct,
    round(avg(value), 0)                                     AS avg_lifetime_value,
    round(avg(orders), 1)                                    AS avg_orders
FROM ltv
GROUP BY segment
ORDER BY revenue DESC;
