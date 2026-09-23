-- A bounded run must distinguish source rows from the subset staged locally.

ALTER TABLE ingestion_runs
    ADD COLUMN IF NOT EXISTS rows_productos_selected INT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_ingestion_selected_product_count_nonnegative'
          AND conrelid = 'ingestion_runs'::regclass
    ) THEN
        ALTER TABLE ingestion_runs
            ADD CONSTRAINT chk_ingestion_selected_product_count_nonnegative CHECK (
                rows_productos_selected IS NULL OR rows_productos_selected >= 0
            );
    END IF;
END
$$;

COMMENT ON COLUMN ingestion_runs.rows_productos_raw IS
    'All non-trailer product rows received from the source ZIP before development bounds.';
COMMENT ON COLUMN ingestion_runs.rows_productos_selected IS
    'Product rows selected into staging after optional development chain and row bounds.';
