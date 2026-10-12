-- =====================================================================
-- Phase 2 / 4: machine-learning customer segments and automated insights
-- =====================================================================

-- Output of the RFM + K-Means segmentation job (data-service/app/analysis).
CREATE TABLE customer_segments (
    customer_id     INT PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    recency_days    INT          NOT NULL,
    frequency       INT          NOT NULL,
    monetary        NUMERIC(14,2) NOT NULL,
    avg_gap_days    NUMERIC(8,1),                 -- mean days between orders (NULL for one-time buyers)
    cluster         SMALLINT     NOT NULL,
    segment_label   VARCHAR(30)  NOT NULL,        -- Champions / Loyal / Needs Attention / At Risk ...
    churn_risk      VARCHAR(10)  NOT NULL CHECK (churn_risk IN ('LOW','MEDIUM','HIGH')),
    computed_at     TIMESTAMP    NOT NULL DEFAULT now()
);
CREATE INDEX idx_customer_segments_label ON customer_segments (segment_label);

CREATE TABLE segmentation_runs (
    id            BIGSERIAL PRIMARY KEY,
    k             SMALLINT     NOT NULL,
    silhouette    NUMERIC(6,4),
    customers     INT          NOT NULL,
    as_of_date    DATE         NOT NULL,
    summary       JSONB        NOT NULL,          -- per-cluster centroids and sizes
    created_at    TIMESTAMP    NOT NULL DEFAULT now()
);

-- Daily insight reports produced by the scheduled job (data-service/app/insights).
CREATE TABLE insight_reports (
    id            BIGSERIAL PRIMARY KEY,
    as_of_date    DATE        NOT NULL,           -- last day of data covered
    generated_at  TIMESTAMP   NOT NULL DEFAULT now(),
    trigger       VARCHAR(20) NOT NULL,           -- schedule / manual / cli
    headline      TEXT        NOT NULL,
    summary       TEXT,                           -- optional LLM-written paragraph
    payload       JSONB       NOT NULL,           -- KPIs, anomalies, SLA stats
    html          TEXT        NOT NULL,           -- rendered e-mail body
    emailed_to    TEXT,
    email_status  VARCHAR(200)
);
CREATE INDEX idx_insight_reports_generated ON insight_reports (generated_at DESC);

-- Segments are useful context for the AI assistant too.
CREATE VIEW ai.customer_segments AS
SELECT cs.customer_id, c.customer_name, c.segment, c.city, c.region,
       cs.recency_days, cs.frequency, cs.monetary, cs.segment_label AS rfm_segment, cs.churn_risk
FROM customer_segments cs
JOIN ai.customers c ON c.customer_id = cs.customer_id;

COMMENT ON VIEW ai.customer_segments IS 'Machine-learning RFM segment and churn risk per customer.';
GRANT SELECT ON ai.customer_segments TO ai_reader;
