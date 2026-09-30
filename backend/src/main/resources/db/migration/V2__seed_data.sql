-- =====================================================================
-- Deterministic synthetic data (24 months: Oct 2024 – Sep 2026)
-- setseed() makes every deploy produce the same dataset.
-- Sized to stay well inside Neon's free-tier storage (~40 MB).
-- =====================================================================
SELECT setseed(0.2026);

-- ---------- Regions & cities ----------
INSERT INTO regions (name) VALUES ('North'), ('South'), ('East'), ('West');

INSERT INTO cities (name, state, region_id, weight)
SELECT c.name, c.state, r.id, c.weight
FROM (VALUES
    ('Delhi',      'Delhi',           'North', 2.6),
    ('Gurugram',   'Haryana',         'North', 1.2),
    ('Jaipur',     'Rajasthan',       'North', 0.9),
    ('Lucknow',    'Uttar Pradesh',   'North', 0.8),
    ('Chandigarh', 'Chandigarh',      'North', 0.6),
    ('Bengaluru',  'Karnataka',       'South', 2.4),
    ('Chennai',    'Tamil Nadu',      'South', 1.6),
    ('Hyderabad',  'Telangana',       'South', 1.8),
    ('Kochi',      'Kerala',          'South', 0.7),
    ('Kolkata',    'West Bengal',     'East',  1.5),
    ('Bhubaneswar','Odisha',          'East',  0.6),
    ('Patna',      'Bihar',           'East',  0.5),
    ('Guwahati',   'Assam',           'East',  0.5),
    ('Mumbai',     'Maharashtra',     'West',  2.8),
    ('Pune',       'Maharashtra',     'West',  1.5),
    ('Ahmedabad',  'Gujarat',         'West',  1.3),
    ('Indore',     'Madhya Pradesh',  'West',  0.6)
) AS c(name, state, region, weight)
JOIN regions r ON r.name = c.region;

-- ---------- Products (8 categories x 15) ----------
WITH cat(category, prefix, lo, hi, margin, nouns) AS (VALUES
    ('Laptops',      'LAP',  32000,  98000, 0.18, ARRAY['ProBook','UltraSlim','WorkStation','Chromebook','Gaming Laptop']),
    ('Smartphones',  'PHN',   8000,  78000, 0.16, ARRAY['Phone','Phone Pro','Phone Lite','Fold','Phone Max']),
    ('Accessories',  'ACC',    399,   6500, 0.45, ARRAY['USB-C Hub','Wireless Mouse','Keyboard','Laptop Bag','Power Bank']),
    ('Audio',        'AUD',    999,  32000, 0.35, ARRAY['Earbuds','Headphones','Soundbar','Speaker','Neckband']),
    ('Monitors',     'MON',   7500,  68000, 0.22, ARRAY['24" Monitor','27" Monitor','Curved Monitor','4K Monitor','Portable Monitor']),
    ('Networking',   'NET',   1200,  28000, 0.30, ARRAY['Router','Mesh System','Switch','Access Point','Range Extender']),
    ('Office Furniture','FUR',4500,  42000, 0.38, ARRAY['Ergo Chair','Standing Desk','Filing Cabinet','Meeting Table','Monitor Arm']),
    ('Software',     'SWL',   1500,  48000, 0.70, ARRAY['Office Suite','Antivirus','Accounting Suite','Design Suite','Backup Plan'])
),
tiers(tier, n) AS (VALUES ('Basic',1),('Plus',2),('Elite',3))
INSERT INTO products (sku, name, category, unit_price, unit_cost)
SELECT
    cat.prefix || '-' || lpad((row_number() OVER (PARTITION BY cat.category ORDER BY noun_idx, tiers.n))::text, 3, '0'),
    'Nova ' || cat.nouns[noun_idx] || ' ' || tiers.tier,
    cat.category,
    price,
    round((price * (1 - cat.margin * (0.8 + random() * 0.4)))::numeric, 2)
FROM cat
CROSS JOIN generate_series(1, 5) AS noun_idx
CROSS JOIN tiers
CROSS JOIN LATERAL (
    SELECT round((cat.lo + (cat.hi - cat.lo) * ((tiers.n - 1) / 3.0 + random() / 3.0))::numeric, -1) AS price
) p;

-- ---------- Name pools ----------
CREATE TEMP TABLE first_names(n) AS SELECT unnest(ARRAY[
 'Aarav','Vivaan','Aditya','Arjun','Sai','Reyansh','Krishna','Ishaan','Rohan','Kabir',
 'Ananya','Diya','Aadhya','Saanvi','Priya','Isha','Meera','Kavya','Riya','Nisha',
 'Rahul','Vikram','Karan','Nikhil','Siddharth','Amit','Suresh','Manoj','Deepak','Varun',
 'Pooja','Sneha','Neha','Shreya','Anjali','Divya','Lakshmi','Swati','Tanvi','Aisha',
 'Farhan','Imran','Zoya','Sara','Harpreet','Gurpreet','Arnav','Dev','Tara','Mira']);
CREATE TEMP TABLE last_names(n) AS SELECT unnest(ARRAY[
 'Sharma','Verma','Gupta','Agarwal','Iyer','Reddy','Nair','Menon','Patel','Shah',
 'Mehta','Joshi','Kulkarni','Deshpande','Rao','Pillai','Banerjee','Chatterjee','Das','Bose',
 'Singh','Kaur','Khan','Mishra','Pandey','Chauhan','Malhotra','Kapoor','Bhatt','Saxena']);

-- ---------- Employees ----------
INSERT INTO employees (full_name, department, city_id, hire_date)
SELECT
    (SELECT n FROM first_names OFFSET floor(random() * 50)::int + (g * 0) LIMIT 1) || ' ' ||
    (SELECT n FROM last_names  OFFSET floor(random() * 30)::int + (g * 0) LIMIT 1),
    CASE WHEN g <= 72 THEN 'Sales' WHEN g <= 124 THEN 'Support' WHEN g <= 148 THEN 'Logistics' ELSE 'Finance' END,
    1 + (g % 17),
    DATE '2018-01-01' + floor(random() * 2400)::int
FROM generate_series(1, 164) AS g;

-- ---------- Customers ----------
-- City chosen proportionally to market weight via cumulative distribution.
CREATE TEMP TABLE city_cdf AS
SELECT id, sum(weight) OVER (ORDER BY id) / sum(weight) OVER () AS cum
FROM cities;

-- Signup dates skew toward recent months so every monthly cohort has real volume.
-- Rows are inserted in signup order, so customer ids ascend with signup date.
INSERT INTO customers (full_name, email, segment, city_id, signup_date)
SELECT fn || ' ' || ln,
       lower(fn) || '.' || lower(ln) || g || '@example.com',
       CASE WHEN s < 0.70 THEN 'Retail' WHEN s < 0.93 THEN 'SMB' ELSE 'Enterprise' END,
       (SELECT id FROM city_cdf WHERE cum >= u ORDER BY cum LIMIT 1),
       signup
FROM (
    SELECT g,
           (SELECT n FROM first_names OFFSET floor(random() * 50)::int + (g * 0) LIMIT 1) AS fn,
           (SELECT n FROM last_names  OFFSET floor(random() * 30)::int + (g * 0) LIMIT 1) AS ln,
           random() AS s,
           random() AS u,
           DATE '2023-01-01' + floor(1368 * power(random(), 0.75))::int AS signup
    FROM generate_series(1, 8000) AS g
) x
ORDER BY signup, g;

-- Number of customers signed up by each day: orders may only pick customers who exist by then.
CREATE TEMP TABLE eligible_by_day AS
SELECT d::date AS day, (SELECT count(*) FROM customers c WHERE c.signup_date <= d::date) AS n
FROM generate_series(DATE '2024-10-01', DATE '2026-09-30', INTERVAL '1 day') AS d;

-- ---------- Orders ----------
-- Daily volume = base x growth trend x festive seasonality x weekday effect x noise.
CREATE TEMP TABLE sales_reps AS
SELECT c.region_id, array_agg(e.id ORDER BY e.id) AS reps
FROM employees e JOIN cities c ON c.id = e.city_id
WHERE e.department = 'Sales'
GROUP BY c.region_id;

INSERT INTO orders (customer_id, sales_rep_id, order_date, channel, status)
SELECT
    cust.id,
    sr.reps[1 + floor(random() * array_length(sr.reps, 1))::int],
    d.day,
    CASE WHEN ch < 0.58 THEN 'Online' WHEN ch < 0.86 THEN 'Store' ELSE 'Partner' END,
    CASE
        WHEN d.day > DATE '2026-09-23' AND st < 0.80 THEN 'SHIPPED'
        WHEN st < 0.905 THEN 'DELIVERED'
        WHEN st < 0.955 THEN 'CANCELLED'
        ELSE 'RETURNED'
    END
FROM (
    SELECT day::date AS day,
           round(
             62
             * (1 + 0.55 * (day::date - DATE '2024-10-01') / 730.0)                            -- growth
             * CASE EXTRACT(MONTH FROM day)
                 WHEN 10 THEN 1.45 WHEN 11 THEN 1.35 WHEN 12 THEN 1.15  -- Diwali / year-end
                 WHEN 1 THEN 0.95 WHEN 3 THEN 1.10                          -- fiscal year-end
                 WHEN 6 THEN 0.85 WHEN 7 THEN 0.88 ELSE 1.0 END
             * CASE EXTRACT(ISODOW FROM day) WHEN 6 THEN 1.18 WHEN 7 THEN 0.72 ELSE 1.0 END
             * (0.85 + random() * 0.30)
           )::int AS n
    FROM generate_series(DATE '2024-10-01', DATE '2026-09-30', INTERVAL '1 day') AS day
) d
JOIN eligible_by_day e ON e.day = d.day
CROSS JOIN LATERAL generate_series(1, d.n) AS k
CROSS JOIN LATERAL (
    -- 15% of orders come from ~300 long-standing key accounts; the rest from any existing
    -- customer, slightly favouring recent sign-ups (newer customers are more active).
    SELECT CASE WHEN random() < 0.15 + k * 0 THEN 1 + floor(300 * random())::int
                ELSE 1 + floor(e.n * power(random(), 0.8))::int END AS id,
           random() AS ch, random() AS st
) cust
JOIN customers cu ON cu.id = cust.id
JOIN cities ci    ON ci.id = cu.city_id
JOIN sales_reps sr ON sr.region_id = ci.region_id;

-- Customers must have signed up before their first order.
UPDATE customers c
SET signup_date = f.first_order - (1 + floor(random() * 60))::int
FROM (SELECT customer_id, min(order_date) AS first_order FROM orders GROUP BY customer_id) f
WHERE f.customer_id = c.id AND c.signup_date > f.first_order;

-- ---------- Order items ----------
CREATE TEMP TABLE product_pick AS
SELECT id, unit_price, category,
       -- popularity order: cheap, frequently bought categories first
       row_number() OVER (ORDER BY array_position(ARRAY['Accessories','Audio','Software','Smartphones',
                          'Networking','Monitors','Office Furniture','Laptops'], category::text), id) AS rn
FROM products;

INSERT INTO order_items (order_id, line_no, product_id, quantity, unit_price, discount)
SELECT
    o.id,
    ln,
    p.id,
    CASE cu.segment
        WHEN 'Enterprise' THEN 2 + floor(random() * 14)::int
        WHEN 'SMB'        THEN 1 + floor(random() * 5)::int
        ELSE 1 + floor(power(random(), 3) * 2)::int
    END,
    p.unit_price,
    CASE WHEN random() < 0.35 THEN round((0.05 + random() * 0.15)::numeric, 3) ELSE 0 END
FROM orders o
JOIN customers cu ON cu.id = o.customer_id
CROSS JOIN LATERAL generate_series(1, 1 + floor(power(random(), 2.2) * 4 + o.id * 0)::int) AS ln
CROSS JOIN LATERAL (
    -- one random draw per line (computed once, then used to look up the product)
    SELECT 1 + floor(power(random(), 1.35) * 120 + ln * 0 + o.id * 0)::int AS rn
) pick
JOIN product_pick p ON p.rn = pick.rn;

-- Roll line totals up to the order header.
UPDATE orders o
SET total_amount = t.amt
FROM (
    SELECT order_id, round(sum(quantity * unit_price * (1 - discount)), 2) AS amt
    FROM order_items GROUP BY order_id
) t
WHERE t.order_id = o.id;

-- ---------- Support tickets ----------
CREATE TEMP TABLE support_agents AS
SELECT array_agg(id ORDER BY id) AS ids FROM employees WHERE department = 'Support';

INSERT INTO tickets (customer_id, order_id, assignee_id, category, priority, status, opened_at, resolved_at, sla_hours)
SELECT
    customer_id, order_id, assignee_id, category, priority,
    CASE WHEN resolved IS NULL THEN (CASE WHEN random() < 0.5 THEN 'OPEN' ELSE 'IN_PROGRESS' END) ELSE 'RESOLVED' END,
    opened_at, resolved, sla_hours
FROM (
    SELECT
        t.*,
        CASE WHEN t.opened_at > TIMESTAMP '2026-09-30 23:59' - make_interval(hours => t.sla_hours * 2)
                  AND random() < 0.6
             THEN NULL
             ELSE t.opened_at + make_interval(mins => (t.sla_hours * 60 * t.speed)::int)
        END AS resolved
    FROM (
        SELECT
            o.customer_id,
            o.id AS order_id,
            sa.ids[1 + floor(random() * array_length(sa.ids, 1))::int] AS assignee_id,
            cat.category,
            pr.priority,
            pr.sla_hours,
            o.order_date + make_interval(days => floor(random() * 10)::int, mins => floor(random() * 1440)::int) AS opened_at,
            -- resolution speed relative to SLA; Delivery & Product Defect run slower
            (0.15 + power(random(), 1.6) * CASE cat.category
                 WHEN 'Delivery' THEN 1.55 WHEN 'Product Defect' THEN 1.45
                 WHEN 'Returns' THEN 1.25 ELSE 1.05 END) AS speed
        FROM orders o
        CROSS JOIN support_agents sa
        CROSS JOIN LATERAL (SELECT random() AS r1, random() AS r2, o.id * 0 AS z) rnd
        CROSS JOIN LATERAL (
            SELECT CASE WHEN rnd.r1 < 0.34 THEN 'Delivery' WHEN rnd.r1 < 0.56 THEN 'Billing'
                        WHEN rnd.r1 < 0.74 THEN 'Product Defect' WHEN rnd.r1 < 0.90 THEN 'Returns'
                        ELSE 'Account' END AS category
        ) cat
        CROSS JOIN LATERAL (
            SELECT CASE WHEN rnd.r2 < 0.40 THEN 'LOW'  WHEN rnd.r2 < 0.75 THEN 'MEDIUM'
                        WHEN rnd.r2 < 0.94 THEN 'HIGH' ELSE 'CRITICAL' END AS priority,
                   CASE WHEN rnd.r2 < 0.40 THEN 72 WHEN rnd.r2 < 0.75 THEN 48
                        WHEN rnd.r2 < 0.94 THEN 24 ELSE 6 END AS sla_hours
        ) pr
        WHERE random() < CASE WHEN o.status = 'RETURNED' THEN 0.9 WHEN o.status = 'CANCELLED' THEN 0.4 ELSE 0.11 END
    ) t
) x
WHERE opened_at <= TIMESTAMP '2026-09-30 23:59';

ANALYZE;
