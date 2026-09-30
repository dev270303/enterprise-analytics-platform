-- New vs returning customers per month (a customer is "new" in the month of their first order).
WITH valid AS (
    SELECT o.customer_id, o.order_date
    FROM orders o
    JOIN customers cu ON cu.id = o.customer_id
    JOIN cities ci    ON ci.id = cu.city_id
    JOIN regions r    ON r.id = ci.region_id
    WHERE o.status IN ('DELIVERED','SHIPPED')
      AND (CAST(:region AS text) IS NULL OR r.name = CAST(:region AS text))
),
first_orders AS (
    SELECT customer_id, date_trunc('month', min(order_date))::date AS first_month
    FROM valid GROUP BY customer_id
),
monthly_active AS (
    SELECT DISTINCT customer_id, date_trunc('month', order_date)::date AS month
    FROM valid
)
SELECT
    m.month,
    count(*) FILTER (WHERE m.month =  f.first_month) AS new_customers,
    count(*) FILTER (WHERE m.month >  f.first_month) AS returning_customers,
    round(100.0 * count(*) FILTER (WHERE m.month > f.first_month) / count(*), 1) AS returning_pct
FROM monthly_active m
JOIN first_orders f USING (customer_id)
GROUP BY m.month
ORDER BY m.month;
