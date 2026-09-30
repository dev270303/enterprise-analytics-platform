-- =====================================================================
-- Enterprise Operations Intelligence — core schema
-- =====================================================================

CREATE TABLE regions (
    id    SMALLSERIAL PRIMARY KEY,
    name  VARCHAR(20) NOT NULL UNIQUE          -- North / South / East / West
);

CREATE TABLE cities (
    id         SMALLSERIAL PRIMARY KEY,
    name       VARCHAR(40) NOT NULL UNIQUE,
    state      VARCHAR(40) NOT NULL,
    region_id  SMALLINT    NOT NULL REFERENCES regions(id),
    weight     NUMERIC(4,2) NOT NULL DEFAULT 1   -- relative market size (seed only)
);

CREATE TABLE products (
    id          SERIAL PRIMARY KEY,
    sku         VARCHAR(20)  NOT NULL UNIQUE,
    name        VARCHAR(80)  NOT NULL,
    category    VARCHAR(30)  NOT NULL,
    unit_price  NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
    unit_cost   NUMERIC(12,2) NOT NULL CHECK (unit_cost > 0),
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE employees (
    id          SERIAL PRIMARY KEY,
    full_name   VARCHAR(80) NOT NULL,
    department  VARCHAR(30) NOT NULL,           -- Sales / Support / Logistics / Finance
    city_id     SMALLINT    NOT NULL REFERENCES cities(id),
    hire_date   DATE        NOT NULL
);

CREATE TABLE customers (
    id           SERIAL PRIMARY KEY,
    full_name    VARCHAR(80)  NOT NULL,
    email        VARCHAR(120) NOT NULL UNIQUE,
    segment      VARCHAR(20)  NOT NULL CHECK (segment IN ('Retail','SMB','Enterprise')),
    city_id      SMALLINT     NOT NULL REFERENCES cities(id),
    signup_date  DATE         NOT NULL
);

CREATE TABLE orders (
    id            BIGSERIAL PRIMARY KEY,
    customer_id   INT          NOT NULL REFERENCES customers(id),
    sales_rep_id  INT          REFERENCES employees(id),
    order_date    DATE         NOT NULL,
    channel       VARCHAR(20)  NOT NULL CHECK (channel IN ('Online','Store','Partner')),
    status        VARCHAR(20)  NOT NULL CHECK (status IN ('DELIVERED','SHIPPED','CANCELLED','RETURNED')),
    total_amount  NUMERIC(14,2) NOT NULL DEFAULT 0
);

CREATE TABLE order_items (
    order_id    BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    line_no     SMALLINT NOT NULL,
    product_id  INT    NOT NULL REFERENCES products(id),
    quantity    INT    NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(12,2) NOT NULL,
    discount    NUMERIC(4,3)  NOT NULL DEFAULT 0 CHECK (discount BETWEEN 0 AND 0.5),
    PRIMARY KEY (order_id, line_no)
);

CREATE TABLE tickets (
    id           BIGSERIAL PRIMARY KEY,
    customer_id  INT         NOT NULL REFERENCES customers(id),
    order_id     BIGINT      REFERENCES orders(id),
    assignee_id  INT         REFERENCES employees(id),
    category     VARCHAR(30) NOT NULL,          -- Delivery / Billing / Product Defect / Returns / Account
    priority     VARCHAR(10) NOT NULL CHECK (priority IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    status       VARCHAR(12) NOT NULL CHECK (status IN ('OPEN','IN_PROGRESS','RESOLVED')),
    opened_at    TIMESTAMP   NOT NULL,
    resolved_at  TIMESTAMP,
    sla_hours    INT         NOT NULL,
    CHECK (resolved_at IS NULL OR resolved_at >= opened_at)
);

CREATE TABLE app_users (
    id             SERIAL PRIMARY KEY,
    username       VARCHAR(40)  NOT NULL UNIQUE,
    password_hash  VARCHAR(100) NOT NULL,
    display_name   VARCHAR(80)  NOT NULL,
    role           VARCHAR(20)  NOT NULL CHECK (role IN ('ADMIN','ANALYST','MANAGER')),
    enabled        BOOLEAN NOT NULL DEFAULT TRUE
);

-- Indexes chosen for the analytical access paths used by the dashboard
CREATE INDEX idx_orders_date          ON orders (order_date);
CREATE INDEX idx_orders_customer_date ON orders (customer_id, order_date);
CREATE INDEX idx_orders_status        ON orders (status);
CREATE INDEX idx_items_product        ON order_items (product_id);
CREATE INDEX idx_customers_city       ON customers (city_id);
CREATE INDEX idx_tickets_opened       ON tickets (opened_at);
CREATE INDEX idx_tickets_assignee     ON tickets (assignee_id);
