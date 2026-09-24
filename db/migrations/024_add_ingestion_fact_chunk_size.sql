-- Record the product-fact batch size used by each run so local benchmark
-- replays remain comparable after environment settings change.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS fact_chunk_size INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_ingestion_fact_chunk_size_positive'
          AND conrelid = 'ingestion_runs'::regclass
    ) THEN
        ALTER TABLE ingestion_runs
            ADD CONSTRAINT chk_ingestion_fact_chunk_size_positive CHECK (
                fact_chunk_size IS NULL OR fact_chunk_size > 0
            );
    END IF;
END
$$;

COMMENT ON COLUMN ingestion_runs.fact_chunk_size IS
    'Product rows prepared and written per fact batch; null for runs before this setting was recorded.';
