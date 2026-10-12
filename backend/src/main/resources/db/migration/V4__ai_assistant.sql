-- =====================================================================
-- Phase 3: AI assistant
--  * "ai" schema: a small set of denormalised, documented, PII-free views.
--    The LLM only ever sees (and can only query) these views.
--  * ai_reader: a NOLOGIN role with SELECT on the ai schema only. The data
--    service switches to it (SET LOCAL ROLE) before running generated SQL,
--    inside a READ ONLY transaction with a statement timeout.
--  * RAG tables: documents, chunks with pgvector embeddings + full-text index.
--  * ai_queries: audit log of every question (also used for rate limiting).
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS ai;

CREATE VIEW ai.orders AS
SELECT o.id            AS order_id,
       o.order_no,
       o.order_date,
       o.status,
       o.channel,
       o.total_amount,
       (o.status IN ('DELIVERED','SHIPPED')) AS is_revenue,
       cu.id           AS customer_id,
       cu.full_name    AS customer_name,
       cu.segment,
       ci.name         AS city,
       ci.state,
       r.name          AS region,
       e.full_name     AS sales_rep
FROM orders o
JOIN customers cu ON cu.id = o.customer_id
JOIN cities ci    ON ci.id = cu.city_id
JOIN regions r    ON r.id = ci.region_id
LEFT JOIN employees e ON e.id = o.sales_rep_id;

COMMENT ON VIEW ai.orders IS 'One row per order (header level).';
COMMENT ON COLUMN ai.orders.total_amount IS 'Order value in INR after discounts.';
COMMENT ON COLUMN ai.orders.is_revenue IS 'TRUE when status is DELIVERED or SHIPPED. Revenue must only count these orders.';
COMMENT ON COLUMN ai.orders.status IS 'DELIVERED, SHIPPED, CANCELLED or RETURNED.';
COMMENT ON COLUMN ai.orders.channel IS 'Online, Store or Partner.';
COMMENT ON COLUMN ai.orders.segment IS 'Customer segment: Retail, SMB or Enterprise.';
COMMENT ON COLUMN ai.orders.region IS 'North, South, East or West.';

CREATE VIEW ai.order_lines AS
SELECT oi.order_id,
       oi.line_no,
       o.order_no,
       o.order_date,
       o.status,
       (o.status IN ('DELIVERED','SHIPPED'))                         AS is_revenue,
       p.sku                                                         AS product_sku,
       p.name                                                        AS product_name,
       p.category,
       oi.quantity,
       oi.unit_price,
       oi.discount,
       round(oi.quantity * oi.unit_price * (1 - oi.discount), 2)     AS line_revenue,
       round(oi.quantity * p.unit_cost, 2)                           AS line_cost,
       cu.id                                                         AS customer_id,
       cu.segment,
       ci.name                                                       AS city,
       r.name                                                        AS region
FROM order_items oi
JOIN orders o     ON o.id = oi.order_id
JOIN products p   ON p.id = oi.product_id
JOIN customers cu ON cu.id = o.customer_id
JOIN cities ci    ON ci.id = cu.city_id
JOIN regions r    ON r.id = ci.region_id;

COMMENT ON VIEW ai.order_lines IS 'One row per product line on an order. Use for product / category level revenue, units and margin.';
COMMENT ON COLUMN ai.order_lines.line_revenue IS 'quantity * unit_price * (1 - discount), INR.';
COMMENT ON COLUMN ai.order_lines.line_cost IS 'quantity * product unit cost, INR. Gross margin = line_revenue - line_cost.';
COMMENT ON COLUMN ai.order_lines.discount IS 'Fraction between 0 and 0.5 (0.10 = 10%).';

CREATE VIEW ai.customers AS
SELECT cu.id        AS customer_id,
       cu.full_name AS customer_name,
       cu.segment,
       cu.signup_date,
       ci.name      AS city,
       ci.state,
       r.name       AS region
FROM customers cu
JOIN cities ci ON ci.id = cu.city_id
JOIN regions r ON r.id = ci.region_id;

COMMENT ON VIEW ai.customers IS 'One row per customer (no contact details).';

CREATE VIEW ai.products AS
SELECT sku, name AS product_name, category, unit_price, unit_cost, active
FROM products;

COMMENT ON VIEW ai.products IS 'Product catalogue with list price and unit cost in INR.';

CREATE VIEW ai.tickets AS
SELECT t.id                                                              AS ticket_id,
       t.ticket_no,
       t.category,
       t.priority,
       t.status,
       t.opened_at,
       t.resolved_at,
       round((EXTRACT(EPOCH FROM (t.resolved_at - t.opened_at)) / 3600.0)::numeric, 1) AS resolution_hours,
       t.sla_hours,
       (t.resolved_at IS NOT NULL AND t.resolved_at > t.opened_at + make_interval(hours => t.sla_hours)) AS sla_breached,
       cu.segment,
       ci.name                                                           AS city,
       r.name                                                            AS region,
       e.full_name                                                       AS assignee
FROM tickets t
JOIN customers cu ON cu.id = t.customer_id
JOIN cities ci    ON ci.id = cu.city_id
JOIN regions r    ON r.id = ci.region_id
LEFT JOIN employees e ON e.id = t.assignee_id;

COMMENT ON VIEW ai.tickets IS 'Customer support tickets. Open tickets have resolved_at NULL.';
COMMENT ON COLUMN ai.tickets.category IS 'Delivery, Billing, Product Defect, Returns or Account.';
COMMENT ON COLUMN ai.tickets.priority IS 'LOW, MEDIUM, HIGH or CRITICAL.';
COMMENT ON COLUMN ai.tickets.sla_breached IS 'TRUE when a resolved ticket took longer than its SLA.';

CREATE VIEW ai.employees AS
SELECT e.employee_code, e.full_name, e.department, e.hire_date, ci.name AS city, r.name AS region
FROM employees e
JOIN cities ci ON ci.id = e.city_id
JOIN regions r ON r.id = ci.region_id;

COMMENT ON VIEW ai.employees IS 'Employees: Sales, Support, Logistics and Finance departments.';

-- ---------- Least-privilege role for generated SQL ----------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ai_reader') THEN
        CREATE ROLE ai_reader NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA ai TO ai_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA ai TO ai_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT SELECT ON TABLES TO ai_reader;

-- Let the application's own login role switch to ai_reader (SET ROLE).
-- Done dynamically: syntax differs slightly across Postgres versions and a managed
-- host may already have granted it. If it cannot be granted, the data service falls
-- back to READ ONLY transactions + SQL validation, and reports that in /health.
DO $$
BEGIN
    EXECUTE format('GRANT ai_reader TO %I', current_user);
EXCEPTION WHEN others THEN
    RAISE NOTICE 'Could not grant ai_reader to %: %', current_user, SQLERRM;
END
$$;

-- ---------- RAG ----------
CREATE TABLE rag_documents (
    id           BIGSERIAL PRIMARY KEY,
    title        VARCHAR(200) NOT NULL,
    source_name  VARCHAR(200) NOT NULL UNIQUE,     -- file name
    content_hash CHAR(64)     NOT NULL,            -- sha256, used to skip re-indexing unchanged files
    chunk_count  INT          NOT NULL DEFAULT 0,
    embedded     BOOLEAN      NOT NULL DEFAULT FALSE,
    uploaded_by  VARCHAR(40),
    created_at   TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at   TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE TABLE rag_chunks (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT  NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    chunk_index  INT     NOT NULL,
    section      VARCHAR(200),
    content      TEXT    NOT NULL,
    embedding    vector(768),                       -- NULL when no embedding provider is configured
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(section, '') || ' ' || content)) STORED,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX idx_rag_chunks_tsv ON rag_chunks USING gin (tsv);
-- Exact (sequential) cosine search is fine for a few thousand chunks; an HNSW index
-- (USING hnsw (embedding vector_cosine_ops)) is the drop-in upgrade for larger corpora.

-- ---------- Audit / rate limiting ----------
CREATE TABLE ai_queries (
    id            BIGSERIAL PRIMARY KEY,
    asked_by      VARCHAR(40),
    question      TEXT        NOT NULL,
    route         VARCHAR(12),                       -- sql / docs / unsupported
    generated_sql TEXT,
    status        VARCHAR(16) NOT NULL,              -- OK / REJECTED / ERROR / NO_ANSWER
    error_message TEXT,
    row_count     INT,
    attempts      INT,
    latency_ms    INT,
    created_at    TIMESTAMP   NOT NULL DEFAULT now()
);
CREATE INDEX idx_ai_queries_created ON ai_queries (created_at DESC);
