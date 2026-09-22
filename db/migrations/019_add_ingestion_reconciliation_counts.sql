-- Keep every product row accountable from staging through atomic publication.
-- Existing aggregate counters remain available for backward compatibility while
-- new runs populate these product/fact-specific values.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS rows_productos_valid INT,
    ADD COLUMN IF NOT EXISTS rows_productos_rejected INT,
    ADD COLUMN IF NOT EXISTS rows_productos_without_keys INT,
    ADD COLUMN IF NOT EXISTS rows_fact_pending INT,
    ADD COLUMN IF NOT EXISTS rows_fact_published INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_ingestion_reconciliation_counts_nonnegative'
          AND conrelid = 'ingestion_runs'::regclass
    ) THEN
        ALTER TABLE ingestion_runs
            ADD CONSTRAINT chk_ingestion_reconciliation_counts_nonnegative CHECK (
                (rows_productos_valid IS NULL OR rows_productos_valid >= 0)
                AND (rows_productos_rejected IS NULL OR rows_productos_rejected >= 0)
                AND (rows_productos_without_keys IS NULL OR rows_productos_without_keys >= 0)
                AND (rows_fact_pending IS NULL OR rows_fact_pending >= 0)
                AND (rows_fact_published IS NULL OR rows_fact_published >= 0)
            );
    END IF;
END
$$;

COMMENT ON COLUMN ingestion_runs.rows_productos_valid IS
    'Product staging rows accepted by cleaning and eligible for fact construction.';
COMMENT ON COLUMN ingestion_runs.rows_productos_rejected IS
    'Product staging rows rejected by cleaning or product-level quality validation.';
COMMENT ON COLUMN ingestion_runs.rows_productos_without_keys IS
    'Valid products that could not resolve the keys required for a fact row.';
COMMENT ON COLUMN ingestion_runs.rows_fact_pending IS
    'Fact rows written to the provisional set before atomic publication.';
COMMENT ON COLUMN ingestion_runs.rows_fact_published IS
    'Fact rows made visible by the completed atomic publication.';
