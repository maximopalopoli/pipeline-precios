-- Mark bounded development ingestions so they can never be mistaken for a
-- complete, publishable daily snapshot.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS is_partial BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS partial_chain_keys TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS partial_max_product_rows INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_partial_ingestion_configuration'
          AND conrelid = 'ingestion_runs'::regclass
    ) THEN
        ALTER TABLE ingestion_runs
            ADD CONSTRAINT chk_partial_ingestion_configuration CHECK (
                partial_max_product_rows IS NULL OR partial_max_product_rows > 0
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_partial_status
    ON ingestion_runs (is_partial, status);

COMMENT ON COLUMN ingestion_runs.is_partial IS
    'True for an explicitly bounded development run; its pending facts are never published.';
COMMENT ON COLUMN ingestion_runs.partial_chain_keys IS
    'Canonical comma-separated id_comercio:id_bandera bounds for a partial run.';
COMMENT ON COLUMN ingestion_runs.partial_max_product_rows IS
    'Maximum staged product rows requested for a partial development run.';
