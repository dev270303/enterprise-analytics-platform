-- =====================================================================
-- Phase 2: data ingestion support
--  * business keys so CSV files from "source systems" can reference rows
--    without knowing our surrogate ids, and so loads are idempotent (upserts)
--  * audit tables for every ETL run and every data-quality issue found
-- =====================================================================

-- ---------- Business keys ----------
ALTER TABLE employees ADD COLUMN employee_code VARCHAR(12);
UPDATE employees SET employee_code = 'EMP-' || lpad(id::text, 4, '0');
ALTER TABLE employees ALTER COLUMN employee_code SET NOT NULL;
ALTER TABLE employees ADD CONSTRAINT uq_employees_code UNIQUE (employee_code);

ALTER TABLE orders ADD COLUMN order_no VARCHAR(24);
UPDATE orders SET order_no = 'ORD-' || lpad(id::text, 7, '0');
ALTER TABLE orders ALTER COLUMN order_no SET NOT NULL;
ALTER TABLE orders ADD CONSTRAINT uq_orders_order_no UNIQUE (order_no);

ALTER TABLE tickets ADD COLUMN ticket_no VARCHAR(24);
UPDATE tickets SET ticket_no = 'TKT-' || lpad(id::text, 7, '0');
ALTER TABLE tickets ALTER COLUMN ticket_no SET NOT NULL;
ALTER TABLE tickets ADD CONSTRAINT uq_tickets_ticket_no UNIQUE (ticket_no);

-- Track which load created or last touched a row (NULL = original seed data)
ALTER TABLE customers   ADD COLUMN etl_run_id BIGINT;
ALTER TABLE products    ADD COLUMN etl_run_id BIGINT;
ALTER TABLE employees   ADD COLUMN etl_run_id BIGINT;
ALTER TABLE orders      ADD COLUMN etl_run_id BIGINT;
ALTER TABLE order_items ADD COLUMN etl_run_id BIGINT;
ALTER TABLE tickets     ADD COLUMN etl_run_id BIGINT;

-- ---------- Audit ----------
CREATE TABLE etl_runs (
    id             BIGSERIAL PRIMARY KEY,
    entity         VARCHAR(20)  NOT NULL,          -- customers / products / employees / orders / order_items / tickets
    file_name      VARCHAR(200) NOT NULL,
    source         VARCHAR(20)  NOT NULL,          -- upload / cli / sample / scheduler
    triggered_by   VARCHAR(40),
    mode           VARCHAR(10)  NOT NULL CHECK (mode IN ('COMMIT','DRY_RUN')),
    status         VARCHAR(12)  NOT NULL CHECK (status IN ('RUNNING','SUCCESS','PARTIAL','FAILED')),
    rows_in        INT NOT NULL DEFAULT 0,
    rows_loaded    INT NOT NULL DEFAULT 0,          -- inserted + updated
    rows_inserted  INT NOT NULL DEFAULT 0,
    rows_updated   INT NOT NULL DEFAULT 0,
    rows_rejected  INT NOT NULL DEFAULT 0,
    rows_duplicate INT NOT NULL DEFAULT 0,
    rows_flagged   INT NOT NULL DEFAULT 0,          -- loaded, but with a warning (e.g. outlier)
    quality_score  NUMERIC(5,2),                    -- % of input rows loaded without any issue
    error_message  TEXT,
    started_at     TIMESTAMP NOT NULL DEFAULT now(),
    finished_at    TIMESTAMP,
    duration_ms    INT
);
CREATE INDEX idx_etl_runs_started ON etl_runs (started_at DESC);

CREATE TABLE etl_issues (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT      NOT NULL REFERENCES etl_runs(id) ON DELETE CASCADE,
    row_number  INT,                                -- 1-based data row in the source file (NULL = file-level)
    severity    VARCHAR(8)  NOT NULL CHECK (severity IN ('ERROR','WARNING','INFO')),
    rule        VARCHAR(40) NOT NULL,               -- e.g. missing_required, invalid_date, unknown_reference
    column_name VARCHAR(60),
    raw_value   TEXT,
    message     TEXT        NOT NULL
);
CREATE INDEX idx_etl_issues_run ON etl_issues (run_id);
