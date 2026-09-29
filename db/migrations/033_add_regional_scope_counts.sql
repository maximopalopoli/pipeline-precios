-- 033_add_regional_scope_counts.sql
-- A regional scope (SEPA_REGIONAL_SCOPE) drops stores outside the region, and
-- their product rows, while staging; product rows of stores the file does not
-- list are dropped too, because they cannot be placed.  Those rows get their
-- own counters so the reconciliation still accounts for every source row.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS regional_scope TEXT,
    ADD COLUMN IF NOT EXISTS rows_sucursales_out_of_scope INT,
    ADD COLUMN IF NOT EXISTS rows_sucursales_unlocated INT,
    ADD COLUMN IF NOT EXISTS rows_productos_out_of_scope INT,
    ADD COLUMN IF NOT EXISTS rows_productos_store_unlisted INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_ingestion_scope_counts_nonnegative'
          AND conrelid = 'ingestion_runs'::regclass
    ) THEN
        ALTER TABLE ingestion_runs
            ADD CONSTRAINT chk_ingestion_scope_counts_nonnegative CHECK (
                (rows_sucursales_out_of_scope IS NULL OR rows_sucursales_out_of_scope >= 0)
                AND (rows_sucursales_unlocated IS NULL OR rows_sucursales_unlocated >= 0)
                AND (rows_productos_out_of_scope IS NULL OR rows_productos_out_of_scope >= 0)
                AND (rows_productos_store_unlisted IS NULL OR rows_productos_store_unlisted >= 0)
            );
    END IF;
END
$$;

COMMENT ON COLUMN ingestion_runs.regional_scope IS
    'Name of the regional scope applied while staging; NULL keeps every store.';
COMMENT ON COLUMN ingestion_runs.rows_sucursales_out_of_scope IS
    'Store rows outside the regional scope, not staged.';
COMMENT ON COLUMN ingestion_runs.rows_sucursales_unlocated IS
    'Store rows in a scoped province without plausible coordinates; not staged, never guessed.';
COMMENT ON COLUMN ingestion_runs.rows_productos_out_of_scope IS
    'Product rows of stores left out by the regional scope (out of scope or unlocated), not staged.';
COMMENT ON COLUMN ingestion_runs.rows_productos_store_unlisted IS
    'Scoped runs only: product rows whose store is not listed in the source sucursales.csv, not staged.';
