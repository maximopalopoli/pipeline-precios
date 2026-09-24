ALTER TABLE ingestion_publication_metrics
    ADD COLUMN IF NOT EXISTS partition_swap_seconds NUMERIC NOT NULL DEFAULT 0
    CHECK (partition_swap_seconds >= 0);

COMMENT ON COLUMN ingestion_publication_metrics.partition_swap_seconds IS
    'Time to detach the previous daily leaf and attach the prepared replacement, including inherited index attachment.';
