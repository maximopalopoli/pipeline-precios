-- A daily snapshot may contain prices observed on earlier days when a chain
-- or store is absent from the current SEPA delivery. Historical rows retain
-- NULL, which means their observation date is their fact date.
ALTER TABLE fact_precios
    ADD COLUMN fecha_observada_id INT;

ALTER TABLE ingestion_runs
    ADD COLUMN rows_fact_carried_forward BIGINT,
    ADD COLUMN rows_fact_snapshot BIGINT;

ALTER TABLE ingestion_runs
    ADD CONSTRAINT chk_ingestion_snapshot_counts CHECK (
        (rows_fact_carried_forward IS NULL OR rows_fact_carried_forward >= 0)
        AND (rows_fact_snapshot IS NULL OR rows_fact_snapshot >= 0)
        AND (rows_fact_snapshot IS NULL OR rows_fact_snapshot =
             rows_fact_published + rows_fact_carried_forward)
    );

COMMENT ON COLUMN fact_precios.fecha_observada_id IS
    'SEPA observation date, which can precede fecha_id for carried prices; NULL on legacy facts means fecha_id.';
COMMENT ON COLUMN ingestion_runs.rows_fact_carried_forward IS
    'Rows copied from an earlier same-scope snapshot because their store had no new prices.';
COMMENT ON COLUMN ingestion_runs.rows_fact_snapshot IS
    'Total visible rows on the published day: rows_fact_published plus rows_fact_carried_forward.';
