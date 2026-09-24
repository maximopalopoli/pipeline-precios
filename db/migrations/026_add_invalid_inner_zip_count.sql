-- Retain a durable audit of malformed per-chain source archives that staging
-- skips. These archives contain no product rows and are therefore outside the
-- product-row reconciliation counters.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS inner_zips_invalid_count INT NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_ingestion_inner_zips_invalid_count_nonnegative'
          AND conrelid = 'ingestion_runs'::regclass
    ) THEN
        ALTER TABLE ingestion_runs
            ADD CONSTRAINT chk_ingestion_inner_zips_invalid_count_nonnegative CHECK (
                inner_zips_invalid_count >= 0
            );
    END IF;
END
$$;

COMMENT ON COLUMN ingestion_runs.inner_zips_invalid_count IS
    'Malformed nested SEPA ZIP archives skipped during staging; no product rows were available to reconcile.';
