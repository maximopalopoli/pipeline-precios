-- 006_create_rejected_rows.sql
-- Registry of rows rejected during quality / transformation processing

CREATE TABLE IF NOT EXISTS rejected_rows (
    id                  SERIAL PRIMARY KEY,
    run_id              INT         NOT NULL REFERENCES ingestion_runs (id),
    source_file         TEXT        NOT NULL,   -- e.g.: sepa_1_comercio-sepa-8_.../productos.csv
    line_number         INT,
    raw_data            TEXT,                   -- raw CSV line
    rejection_reason    TEXT        NOT NULL,
    rejected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rejected_rows_run    ON rejected_rows (run_id);
CREATE INDEX IF NOT EXISTS idx_rejected_rows_source ON rejected_rows (source_file);
