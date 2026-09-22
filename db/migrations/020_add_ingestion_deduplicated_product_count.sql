-- A valid product row can be intentionally replaced by a later duplicate for
-- the same product × store. Track that outcome separately from missing keys.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS rows_productos_deduplicated INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_ingestion_deduplicated_count_nonnegative'
          AND conrelid = 'ingestion_runs'::regclass
    ) THEN
        ALTER TABLE ingestion_runs
            ADD CONSTRAINT chk_ingestion_deduplicated_count_nonnegative CHECK (
                rows_productos_deduplicated IS NULL
                OR rows_productos_deduplicated >= 0
            );
    END IF;
END
$$;

COMMENT ON COLUMN ingestion_runs.rows_productos_deduplicated IS
    'Valid product rows replaced by a later duplicate at the product and store fact grain.';
