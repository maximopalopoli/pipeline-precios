-- 025_create_ingestion_fact_load_metrics.sql
-- Durable performance measurements for the streamed product-fact stage.

CREATE TABLE IF NOT EXISTS ingestion_fact_load_metrics (
    run_id          INT PRIMARY KEY REFERENCES ingestion_runs (id) ON DELETE CASCADE,
    elapsed_seconds NUMERIC NOT NULL CHECK (elapsed_seconds >= 0),
    peak_rss_bytes  BIGINT CHECK (peak_rss_bytes IS NULL OR peak_rss_bytes >= 0),
    chunks          INT NOT NULL CHECK (chunks >= 0),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE ingestion_fact_load_metrics IS
    'Durable fact-load timings and process memory high-water marks for ETL comparisons.';
