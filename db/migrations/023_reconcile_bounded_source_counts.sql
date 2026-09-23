-- Preserve the product reconciliation invariant for bounded runs while
-- retaining the broader source-selection audit.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS rows_productos_source INT;

-- Migration 022 recorded source rows in `raw` and staged rows in `selected`.
-- Reclassify those completed/current records before using the corrected terms:
-- source >= selected >= raw (the rows actually staged for cleaning).
UPDATE ingestion_runs
   SET rows_productos_source = rows_productos_raw,
       rows_productos_raw = rows_productos_selected,
       rows_productos_selected = rows_productos_raw
 WHERE rows_productos_selected IS NOT NULL;

UPDATE ingestion_runs
   SET rows_productos_source = rows_productos_raw
 WHERE rows_productos_source IS NULL
   AND rows_productos_raw IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_ingestion_source_product_count_nonnegative'
          AND conrelid = 'ingestion_runs'::regclass
    ) THEN
        ALTER TABLE ingestion_runs
            ADD CONSTRAINT chk_ingestion_source_product_count_nonnegative CHECK (
                rows_productos_source IS NULL OR rows_productos_source >= 0
            );
    END IF;
END
$$;

COMMENT ON COLUMN ingestion_runs.rows_productos_source IS
    'Product rows inspected in the source archives before development chain bounds.';
COMMENT ON COLUMN ingestion_runs.rows_productos_selected IS
    'Source product rows selected by development chain bounds before the row cap.';
COMMENT ON COLUMN ingestion_runs.rows_productos_raw IS
    'Product rows staged after all development bounds; the input to product cleaning.';
