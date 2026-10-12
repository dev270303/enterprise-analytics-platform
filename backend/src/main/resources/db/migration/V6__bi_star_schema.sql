-- =====================================================================
-- Phase 2: BI layer for Power BI / any BI tool.
-- A star schema exposed as views in the "bi" schema: one sales fact at order-line
-- grain, one support fact, and conformed dimensions (date, customer, product,
-- geography, employee). BI tools import these instead of the raw OLTP tables.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS bi;

CREATE VIEW bi.dim_date AS
SELECT d::date                                   AS date_key,
       EXTRACT(YEAR FROM d)::int                 AS year,
       EXTRACT(QUARTER FROM d)::int              AS quarter,
       EXTRACT(MONTH FROM d)::int                AS month,
       to_char(d, 'Mon YYYY')                    AS month_label,
       date_trunc('month', d)::date              AS month_start,
       EXTRACT(ISODOW FROM d)::int               AS iso_weekday,
       to_char(d, 'Dy')                          AS weekday_name,
       (EXTRACT(ISODOW FROM d) IN (6, 7))        AS is_weekend,
       -- Indian fiscal year runs April-March: FY2026 = Apr 2025 - Mar 2026
       CASE WHEN EXTRACT(MONTH FROM d) >= 4 THEN EXTRACT(YEAR FROM d)::int + 1
            ELSE EXTRACT(YEAR FROM d)::int END   AS fiscal_year
FROM generate_series(DATE '2023-01-01', DATE '2027-12-31', INTERVAL '1 day') AS d;

CREATE VIEW bi.dim_geography AS
SELECT ci.id AS city_key, ci.name AS city, ci.state, r.name AS region
FROM cities ci JOIN regions r ON r.id = ci.region_id;

CREATE VIEW bi.dim_customer AS
SELECT cu.id AS customer_key, cu.full_name AS customer_name, cu.segment, cu.signup_date,
       cu.city_id AS city_key,
       coalesce(cs.segment_label, 'Unscored') AS rfm_segment,
       coalesce(cs.churn_risk, 'UNKNOWN')     AS churn_risk
FROM customers cu
LEFT JOIN customer_segments cs ON cs.customer_id = cu.id;

CREATE VIEW bi.dim_product AS
SELECT id AS product_key, sku, name AS product_name, category, unit_price AS list_price, unit_cost
FROM products;

CREATE VIEW bi.dim_employee AS
SELECT id AS employee_key, employee_code, full_name, department, city_id AS city_key, hire_date
FROM employees;

CREATE VIEW bi.fact_sales AS
SELECT oi.order_id,
       oi.line_no,
       o.order_no,
       o.order_date                                              AS date_key,
       o.customer_id                                             AS customer_key,
       cu.city_id                                                AS city_key,
       oi.product_id                                             AS product_key,
       o.sales_rep_id                                            AS employee_key,
       o.channel,
       o.status,
       (o.status IN ('DELIVERED','SHIPPED'))                     AS is_revenue,
       oi.quantity,
       oi.unit_price,
       oi.discount,
       round(oi.quantity * oi.unit_price * (1 - oi.discount), 2) AS net_revenue,
       round(oi.quantity * p.unit_cost, 2)                       AS cost
FROM order_items oi
JOIN orders o     ON o.id = oi.order_id
JOIN customers cu ON cu.id = o.customer_id
JOIN products p   ON p.id = oi.product_id;

CREATE VIEW bi.fact_tickets AS
SELECT t.id                                       AS ticket_key,
       t.ticket_no,
       t.opened_at::date                          AS date_key,
       t.customer_id                              AS customer_key,
       cu.city_id                                 AS city_key,
       t.assignee_id                              AS employee_key,
       t.category, t.priority, t.status, t.sla_hours,
       round((EXTRACT(EPOCH FROM (t.resolved_at - t.opened_at)) / 3600.0)::numeric, 2) AS resolution_hours,
       CASE WHEN t.resolved_at IS NULL THEN NULL
            ELSE t.resolved_at > t.opened_at + make_interval(hours => t.sla_hours) END AS sla_breached
FROM tickets t
JOIN customers cu ON cu.id = t.customer_id;
