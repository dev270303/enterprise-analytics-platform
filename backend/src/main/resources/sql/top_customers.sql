-- Top customers by lifetime value, with recency and value quartile (NTILE).
WITH anchor AS (SELECT max(order_date) AS end_date FROM orders),
customer_value AS (
    SELECT cu.id, cu.full_name, cu.segment, ci.name AS city, r.name AS region,
           count(*)               AS orders,
           sum(o.total_amount)    AS lifetime_value,
           min(o.order_date)      AS first_order,
           max(o.order_date)      AS last_order
    FROM orders o
    JOIN customers cu ON cu.id = o.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    WHERE o.status IN ('DELIVERED','SHIPPED')
      AND (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
    GROUP BY cu.id, cu.full_name, cu.segment, ci.name, r.name
),
scored AS (
    SELECT cv.*,
           ntile(4) OVER (ORDER BY lifetime_value DESC)  AS value_quartile,
           rank()   OVER (ORDER BY lifetime_value DESC)  AS ltv_rank
    FROM customer_value cv
)
SELECT ltv_rank, full_name, segment, city, region, orders,
       round(lifetime_value, 2)                   AS lifetime_value,
       round(lifetime_value / orders, 2)          AS avg_order_value,
       last_order,
       (SELECT end_date FROM anchor) - last_order AS days_since_last_order,
       value_quartile
FROM scored
WHERE ltv_rank <= 10
ORDER BY ltv_rank;
